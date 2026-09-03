//! Questions through a desktop dialog tool, for a double-click in a file
//! manager where there is no terminal: what a Steam Deck user in Desktop
//! mode will do. One abstraction, three dialects: zenity, yad (a zenity
//! descendant with its own option names: no `--question`, `--no-headers`
//! for a list, buttons declared by hand) and kdialog. All three report
//! yes/no in the exit code and print a choice on standard output. X11 or
//! Wayland makes no difference.
//!
//! Probe order, by presence on the PATH, with no desktop detection beyond
//! one variable: on KDE (`XDG_CURRENT_DESKTOP` contains `KDE`) kdialog,
//! zenity, yad; otherwise zenity, yad, kdialog.

use std::path::PathBuf;
use std::process::{Command, Stdio};

use crate::ui::Ui;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Kind {
    Zenity,
    Yad,
    Kdialog,
}

pub struct Dialog {
    kind: Kind,
    path: PathBuf,
    title: String,
}

/// The first dialog tool on the PATH, if any.
pub fn find(title: &str) -> Option<Dialog> {
    let kde = std::env::var("XDG_CURRENT_DESKTOP").map(|d| d.to_uppercase().contains("KDE")).unwrap_or(false);
    let order: [(Kind, &str); 3] = if kde {
        [(Kind::Kdialog, "kdialog"), (Kind::Zenity, "zenity"), (Kind::Yad, "yad")]
    } else {
        [(Kind::Zenity, "zenity"), (Kind::Yad, "yad"), (Kind::Kdialog, "kdialog")]
    };
    for (kind, name) in order {
        if let Some(path) = on_path(name) {
            return Some(Dialog { kind, path, title: title.to_string() });
        }
    }
    None
}

fn on_path(name: &str) -> Option<PathBuf> {
    let path = std::env::var_os("PATH")?;
    for dir in std::env::split_paths(&path) {
        let candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate);
        }
    }
    None
}

impl Dialog {
    fn run(&self, args: &[String]) -> Option<(i32, String)> {
        let out = Command::new(&self.path).args(args).stdin(Stdio::null()).stderr(Stdio::null()).output().ok()?;
        Some((out.status.code().unwrap_or(-1), String::from_utf8_lossy(&out.stdout).trim().to_string()))
    }

    /// The transcript holds game paths, which zenity and yad would otherwise
    /// read as Pango markup: an `&` in a folder name blanks the dialog.
    fn message(&self, text: &str, error: bool) {
        let args: Vec<String> = match self.kind {
            Kind::Zenity => vec![
                if error { "--error" } else { "--info" }.into(),
                format!("--title={}", self.title),
                "--width=520".into(),
                "--no-markup".into(),
                format!("--text={text}"),
            ],
            Kind::Yad => vec![
                format!("--title={}", self.title),
                "--width=520".into(),
                "--no-markup".into(),
                format!("--image={}", if error { "dialog-error" } else { "dialog-information" }),
                format!("--text={text}"),
                "--button=OK:0".into(),
            ],
            Kind::Kdialog => vec![
                format!("--title={}", self.title),
                if error { "--error" } else { "--msgbox" }.into(),
                text.to_string(),
            ],
        };
        let _ = self.run(&args);
    }
}

impl Ui for Dialog {
    fn ask_yes(&mut self, question: &str, default: bool) -> Option<bool> {
        let args: Vec<String> = match self.kind {
            Kind::Zenity => {
                let mut a = vec!["--question".to_string(), format!("--title={}", self.title), "--width=520".into(), format!("--text={question}")];
                if !default {
                    a.push("--default-cancel".into());
                }
                a
            }
            Kind::Yad => vec![
                format!("--title={}", self.title),
                "--width=520".into(),
                "--image=dialog-question".into(),
                format!("--text={question}"),
                "--button=Yes:0".into(),
                "--button=No:1".into(),
            ],
            Kind::Kdialog => vec![
                format!("--title={}", self.title),
                if default { "--yesno" } else { "--warningyesno" }.into(),
                question.to_string(),
            ],
        };
        match self.run(&args)?.0 {
            0 => Some(true),
            1 => Some(false),
            _ => None,
        }
    }

    fn choose(&mut self, title: &str, items: &[String], default: Option<usize>) -> Option<usize> {
        let args: Vec<String> = match self.kind {
            Kind::Zenity | Kind::Yad => {
                let mut a = vec![
                    "--list".to_string(),
                    format!("--title={}", self.title),
                    "--width=520".into(),
                    "--height=420".into(),
                    format!("--text={title}"),
                    "--column=Choice".into(),
                    if self.kind == Kind::Zenity { "--hide-header" } else { "--no-headers" }.into(),
                ];
                a.extend(items.iter().cloned());
                a
            }
            Kind::Kdialog => {
                let mut a = vec![format!("--title={}", self.title), "--menu".to_string(), title.to_string()];
                for (i, item) in items.iter().enumerate() {
                    a.push((i + 1).to_string());
                    a.push(item.clone());
                }
                if let Some(d) = default {
                    a.push("--default".into());
                    a.push((d + 1).to_string());
                }
                a
            }
        };
        let (code, out) = self.run(&args)?;
        if code != 0 {
            return None;
        }
        match self.kind {
            Kind::Kdialog => out.parse::<usize>().ok().and_then(|n| n.checked_sub(1)).filter(|&i| i < items.len()),
            // yad ends every row it prints with its column separator, `|`
            _ => {
                let out = out.trim_end_matches('|');
                items.iter().position(|i| i == out).or_else(|| items.iter().position(|i| out.starts_with(i.as_str())))
            }
        }
    }

    fn ask_text(&mut self, prompt: &str) -> Option<String> {
        let args: Vec<String> = match self.kind {
            Kind::Zenity | Kind::Yad => {
                vec!["--entry".to_string(), format!("--title={}", self.title), "--width=520".into(), format!("--text={prompt}")]
            }
            Kind::Kdialog => vec![format!("--title={}", self.title), "--inputbox".to_string(), prompt.to_string()],
        };
        let (code, out) = self.run(&args)?;
        (code == 0).then_some(out)
    }

    fn finish(&mut self, ok: bool, summary: &str) {
        self.message(summary, !ok);
    }
}
