// Life is Strange: Double Exposure - Ultrawide Fix (Windows front-end)
//
// This is deliberately a THIN front-end. It contains no patch logic of its own:
// every option below runs patcher.py, which is the single source of truth for
// what gets written to the executable, the game data files and Engine.ini.
// An earlier version of this file reimplemented the byte patches in C# and
// silently drifted out of sync with the Python one, which is exactly the bug
// this structure prevents.
//
// It prefers `uv run`, which needs no virtualenv and fetches the one optional
// dependency (blake3, used by the game-files step) automatically. If uv is not
// installed it falls back to the Python launcher and then to plain python.
//
// Build (stock .NET Framework compiler, no SDK required):
//   %WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe /target:winexe ^
//     /out:LiSUltrawidePatcher.exe LiSUltrawidePatcher.cs ^
//     /r:System.dll /r:System.Windows.Forms.dll /r:System.Drawing.dll

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Windows.Forms;

namespace LiSUltrawidePatcher
{
    public class MainForm : Form
    {
        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();

        private struct Preset
        {
            public string Name;
            public int W, H;
            public Preset(string n, int w, int h) { Name = n; W = w; H = h; }
        }

        private readonly List<Preset> presets = new List<Preset>
        {
            new Preset("5120x2160 (21:9 WUHD 4K)", 5120, 2160),
            new Preset("3440x1440 (21:9 UWQHD)", 3440, 1440),
            new Preset("2560x1080 (21:9 UWD)", 2560, 1080),
            new Preset("3840x1600 (24:10 UW)", 3840, 1600),
            new Preset("5120x1440 (32:9 Super Ultrawide)", 5120, 1440),
            new Preset("3840x1080 (32:9 Super Ultrawide)", 3840, 1080),
            new Preset("7680x2160 (32:9 Super Ultrawide)", 7680, 2160),
            new Preset("3840x1200 (32:10)", 3840, 1200),
            new Preset("2560x1600 (16:10)", 2560, 1600),
        };

        private TextBox txtExePath, txtWidth, txtHeight, txtLog;
        private ComboBox cmbPresets;
        private CheckBox chkExe, chkGameFiles, chkChromatic, chkSharpen;
        private Button btnBrowse, btnInstall, btnRestore;
        private Label lblCustom, lblX;

        public MainForm()
        {
            InitializeComponent();
            AutoDetect();
        }

        // ------------------------------------------------------------------ UI

        private Label Header(string text, int y)
        {
            Label l = new Label();
            l.Text = text;
            l.Location = new Point(16, y);
            l.Size = new Size(560, 18);
            l.Font = new Font("Segoe UI", 9F, FontStyle.Bold);
            Controls.Add(l);
            return l;
        }

        private CheckBox Option(string title, string detail, int y)
        {
            CheckBox c = new CheckBox();
            c.Text = title;
            c.Checked = true;
            c.Location = new Point(20, y);
            c.Size = new Size(560, 20);
            c.Font = new Font("Segoe UI", 9F, FontStyle.Bold);
            Controls.Add(c);

            Label d = new Label();
            d.Text = detail;
            d.Location = new Point(38, y + 19);
            d.Size = new Size(548, 32);
            d.ForeColor = SystemColors.GrayText;
            Controls.Add(d);
            return c;
        }

        private void InitializeComponent()
        {
            Text = "Life is Strange: Double Exposure - Ultrawide Fix";
            ClientSize = new Size(600, 660);
            FormBorderStyle = FormBorderStyle.FixedSingle;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Segoe UI", 9F);

            Header("Game executable", 12);
            txtExePath = new TextBox();
            txtExePath.Location = new Point(16, 32);
            txtExePath.Size = new Size(480, 23);
            Controls.Add(txtExePath);

            btnBrowse = new Button();
            btnBrowse.Text = "Browse...";
            btnBrowse.Location = new Point(504, 31);
            btnBrowse.Size = new Size(80, 25);
            btnBrowse.Click += OnBrowse;
            Controls.Add(btnBrowse);

            Header("Display resolution", 68);
            cmbPresets = new ComboBox();
            cmbPresets.DropDownStyle = ComboBoxStyle.DropDownList;
            cmbPresets.Location = new Point(16, 88);
            cmbPresets.Size = new Size(360, 23);
            foreach (Preset p in presets) cmbPresets.Items.Add(p.Name);
            cmbPresets.Items.Add("Custom...");
            cmbPresets.SelectedIndexChanged += OnPresetChanged;
            Controls.Add(cmbPresets);

            lblCustom = new Label();
            lblCustom.Text = "Custom:";
            lblCustom.Location = new Point(386, 91);
            lblCustom.Size = new Size(50, 20);
            Controls.Add(lblCustom);

            txtWidth = new TextBox();
            txtWidth.Location = new Point(436, 88);
            txtWidth.Size = new Size(60, 23);
            Controls.Add(txtWidth);

            lblX = new Label();
            lblX.Text = "x";
            lblX.Location = new Point(500, 91);
            lblX.Size = new Size(12, 20);
            Controls.Add(lblX);

            txtHeight = new TextBox();
            txtHeight.Location = new Point(514, 88);
            txtHeight.Size = new Size(60, 23);
            Controls.Add(txtHeight);

            Header("What to install", 124);

            chkExe = Option("Ultrawide camera",
                "Hor+ cutscenes, dialogue and exploration: the full 16:9 vertical framing is\n"
                + "kept and the view is widened. No black bars, no zoom when a dialogue ends.",
                146);

            chkGameFiles = Option("Full-width UI",
                "Loading screens cover the whole screen instead of leaving the world visible at\n"
                + "the sides, and the HUD sits on the real screen edge. Patches game data files.",
                204);

            chkChromatic = Option("Disable chromatic aberration",
                "Removes the colour fringing, which is most obvious at the widened edges.",
                262);

            chkSharpen = Option("Reduce blurriness",
                "Applies the recommended TSR (temporal upscaler) settings for this resolution.",
                308);

            Label note = new Label();
            note.Text = "The last two write a single clearly-marked block into your Engine.ini. "
                      + "Restore removes it again.";
            note.Location = new Point(20, 348);
            note.Size = new Size(560, 32);
            note.ForeColor = SystemColors.GrayText;
            Controls.Add(note);

            btnInstall = new Button();
            btnInstall.Text = "Install";
            btnInstall.Location = new Point(16, 386);
            btnInstall.Size = new Size(280, 34);
            btnInstall.Font = new Font("Segoe UI", 10F, FontStyle.Bold);
            btnInstall.Click += OnInstall;
            Controls.Add(btnInstall);

            btnRestore = new Button();
            btnRestore.Text = "Restore original";
            btnRestore.Location = new Point(304, 386);
            btnRestore.Size = new Size(280, 34);
            btnRestore.Click += OnRestore;
            Controls.Add(btnRestore);

            txtLog = new TextBox();
            txtLog.Multiline = true;
            txtLog.ScrollBars = ScrollBars.Vertical;
            txtLog.ReadOnly = true;
            txtLog.BackColor = Color.White;
            txtLog.Font = new Font("Consolas", 8.5F);
            txtLog.Location = new Point(16, 432);
            txtLog.Size = new Size(568, 210);
            Controls.Add(txtLog);
        }

        // ------------------------------------------------------------- helpers

        private void Log(string s)
        {
            if (txtLog.InvokeRequired)
            {
                txtLog.BeginInvoke((MethodInvoker)delegate { Log(s); });
                return;
            }
            txtLog.AppendText(s + Environment.NewLine);
        }

        private void OnPresetChanged(object sender, EventArgs e)
        {
            bool custom = cmbPresets.SelectedIndex == presets.Count;
            txtWidth.Enabled = txtHeight.Enabled = custom;
            if (!custom && cmbPresets.SelectedIndex >= 0)
            {
                Preset p = presets[cmbPresets.SelectedIndex];
                txtWidth.Text = p.W.ToString();
                txtHeight.Text = p.H.ToString();
            }
        }

        private void OnBrowse(object sender, EventArgs e)
        {
            OpenFileDialog d = new OpenFileDialog();
            d.Filter = "Chronos-Win64-Shipping.exe|Chronos-Win64-Shipping.exe|All files|*.*";
            if (d.ShowDialog() == DialogResult.OK) txtExePath.Text = d.FileName;
        }

        private void AutoDetect()
        {
            try { SetProcessDPIAware(); }
            catch { }

            // executable: look beside this tool and one level up
            string[] guesses = {
                "Chronos-Win64-Shipping.exe",
                Path.Combine("Chronos", "Binaries", "Win64", "Chronos-Win64-Shipping.exe"),
                Path.Combine("..", "Chronos", "Binaries", "Win64", "Chronos-Win64-Shipping.exe"),
            };
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            foreach (string g in guesses)
            {
                string full = Path.GetFullPath(Path.Combine(baseDir, g));
                if (File.Exists(full)) { txtExePath.Text = full; break; }
            }
            if (txtExePath.Text.Length == 0)
                Log("Could not find the game automatically - use Browse.");

            // resolution: select the preset matching this display, else Custom
            Rectangle b = Screen.PrimaryScreen.Bounds;
            txtWidth.Text = b.Width.ToString();
            txtHeight.Text = b.Height.ToString();
            int match = -1;
            for (int i = 0; i < presets.Count; i++)
                if (presets[i].W == b.Width && presets[i].H == b.Height) { match = i; break; }
            cmbPresets.SelectedIndex = (match >= 0) ? match : presets.Count;
            Log(string.Format("Detected display: {0}x{1}{2}", b.Width, b.Height,
                              match >= 0 ? "" : "  (using Custom)"));
        }

        // uv is the preferred runner: it needs no virtualenv, fetches the one
        // optional dependency (blake3) itself, AND will download a Python
        // interpreter if the machine has none - so fetching uv alone is enough
        // to make everything work on a bare system.
        // The official one-liner from https://astral.sh/uv.
        private const string UvInstallCommand =
            "-ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\"";

        private bool uvOffered;
        private bool oodleOffered;
        private bool fetchOodle;

        /// <summary>True if an Oodle DLL is already sitting in tools/assetdump.</summary>
        private static bool OodleAlreadyPresent()
        {
            string dir = Path.Combine(AppDomain.CurrentDomain.BaseDirectory,
                                      Path.Combine("tools", "assetdump"));
            if (!Directory.Exists(dir)) return false;
            foreach (string pattern in new[] { "oodle-data-shared.dll", "oo2core_*.dll" })
            {
                try
                {
                    if (Directory.GetFiles(dir, pattern).Length > 0) return true;
                }
                catch { }
            }
            return false;
        }

        /// <summary>uv from PATH, or from the locations its installer uses.</summary>
        private static string FindUv()
        {
            string onPath = Which("uv");
            if (onPath != null) return onPath;
            // A freshly installed uv is not on THIS process's PATH, which was
            // captured at launch - so look where the installer puts it.
            string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            string[] dirs = {
                Path.Combine(home, ".local", "bin"),
                Path.Combine(home, ".cargo", "bin"),
            };
            foreach (string d in dirs)
            {
                try
                {
                    string f = Path.Combine(d, "uv.exe");
                    if (File.Exists(f)) return f;
                }
                catch { }
            }
            return null;
        }

        /// <summary>Ask, then run uv's official installer. Returns its path or null.</summary>
        private string OfferToFetchUv(string reason)
        {
            DialogResult r = MessageBox.Show(
                reason + "\r\n\r\n"
                + "Install uv now? This runs the official one-line installer from "
                + "astral.sh:\r\n\r\n"
                + "    powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\"\r\n\r\n"
                + "It installs for your user account only - no administrator rights "
                + "and no system-wide changes. uv then downloads a Python interpreter "
                + "by itself if this machine does not have one, so this is all that "
                + "is needed.",
                "Install uv?", MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (r != DialogResult.Yes) return null;

            try
            {
                Log("Running the uv installer...");
                Application.DoEvents();

                ProcessStartInfo psi = new ProcessStartInfo("powershell", UvInstallCommand);
                psi.UseShellExecute = false;
                psi.CreateNoWindow = true;
                psi.RedirectStandardOutput = true;
                psi.RedirectStandardError = true;

                using (Process p = Process.Start(psi))
                {
                    p.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
                    { if (e.Data != null) Log("  " + e.Data); };
                    p.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
                    { if (e.Data != null) Log("  " + e.Data); };
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                    p.WaitForExit();
                }

                string uv = FindUv();
                if (uv != null)
                {
                    Log("uv ready: " + uv);
                    return uv;
                }
                Log("uv was not found after the installer finished.");
                MessageBox.Show(
                    "The installer ran but uv could not be found afterwards.\r\n\r\n"
                    + "Try opening a new terminal and running 'uv --version', or "
                    + "install Python 3.8+ and put it on PATH.",
                    "uv not found", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
            catch (Exception ex)
            {
                Log("Install failed: " + ex.Message);
                MessageBox.Show(
                    "Could not run the uv installer:\r\n\r\n" + ex.Message + "\r\n\r\n"
                    + "You can install it manually from https://astral.sh/uv, or "
                    + "install Python 3.8+ and put it on PATH.",
                    "Install failed", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
            return null;
        }

        /// <summary>Resolve how to run Python: uv first, then py, then python.</summary>
        private bool ResolveRunner(string script, List<string> argv,
                                   out string exe, out string args)
        {
            StringBuilder tail = new StringBuilder();
            tail.Append('"').Append(script).Append('"');
            foreach (string a in argv) tail.Append(' ').Append(a);

            string uv = FindUv();
            if (uv != null)
            {
                exe = uv;
                args = "run --quiet --script " + tail;
                return true;
            }
            if (Which("py") != null)
            {
                exe = "py";
                args = "-3 " + tail;
                return true;
            }
            if (Which("python") != null)
            {
                exe = "python";
                args = tail.ToString();
                return true;
            }
            exe = args = null;
            return false;
        }

        private static string Which(string name)
        {
            string path = Environment.GetEnvironmentVariable("PATH");
            if (path == null) return null;
            foreach (string dir in path.Split(';'))
            {
                if (dir.Length == 0) continue;
                foreach (string ext in new[] { ".exe", ".cmd", ".bat" })
                {
                    try
                    {
                        string f = Path.Combine(dir.Trim('"'), name + ext);
                        if (File.Exists(f)) return f;
                    }
                    catch { }
                }
            }
            return null;
        }

        private string ScriptPath()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string p = Path.Combine(baseDir, "patcher.py");
            return File.Exists(p) ? p : null;
        }

        private void Run(List<string> argv)
        {
            string script = ScriptPath();
            if (script == null)
            {
                MessageBox.Show("patcher.py must sit next to this program.",
                                "Missing patcher.py", MessageBoxButtons.OK,
                                MessageBoxIcon.Error);
                return;
            }
            string exe, args;
            if (!ResolveRunner(script, argv, out exe, out args))
            {
                if (OfferToFetchUv("Neither uv nor Python was found on this computer.")
                        == null)
                {
                    MessageBox.Show(
                        "Nothing to run the patcher with.\r\n\r\n"
                        + "Install uv (https://astral.sh/uv) - recommended - or "
                        + "install Python 3.8+ and make sure it is on PATH.",
                        "Python not found", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }
                ResolveRunner(script, argv, out exe, out args);
            }

            btnInstall.Enabled = btnRestore.Enabled = false;
            txtLog.Clear();
            Log("> " + exe + " " + args);
            Log("");

            ProcessStartInfo psi = new ProcessStartInfo(exe, args);
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            psi.WorkingDirectory = Path.GetDirectoryName(script);

            Process proc = new Process();
            proc.StartInfo = psi;
            proc.EnableRaisingEvents = true;
            proc.OutputDataReceived += delegate(object s, DataReceivedEventArgs e)
            {
                if (e.Data != null) Log(e.Data);
            };
            proc.ErrorDataReceived += delegate(object s, DataReceivedEventArgs e)
            {
                if (e.Data != null) Log(e.Data);
            };
            proc.Exited += delegate
            {
                Log("");
                Log(proc.ExitCode == 0 ? "Finished." : "Failed (exit code " + proc.ExitCode + ").");
                BeginInvoke((MethodInvoker)delegate
                {
                    btnInstall.Enabled = btnRestore.Enabled = true;
                });
            };
            try
            {
                proc.Start();
                proc.BeginOutputReadLine();
                proc.BeginErrorReadLine();
            }
            catch (Exception ex)
            {
                Log("Could not start " + exe + ": " + ex.Message);
                btnInstall.Enabled = btnRestore.Enabled = true;
            }
        }

        private bool CommonArgs(List<string> argv)
        {
            if (txtExePath.Text.Trim().Length == 0)
            {
                MessageBox.Show("Select the game executable first.", "No executable",
                                MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
            int w, h;
            if (!int.TryParse(txtWidth.Text.Trim(), out w) ||
                !int.TryParse(txtHeight.Text.Trim(), out h) || w <= 0 || h <= 0)
            {
                MessageBox.Show("Enter a valid resolution.", "Invalid resolution",
                                MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return false;
            }
            argv.Add("--exe");
            argv.Add("\"" + txtExePath.Text.Trim() + "\"");
            argv.Add("--width");
            argv.Add(w.ToString());
            argv.Add("--height");
            argv.Add(h.ToString());
            argv.Add("--yes");
            return true;
        }

        private void OnInstall(object sender, EventArgs e)
        {
            List<string> argv = new List<string>();
            if (!CommonArgs(argv)) return;
            if (!chkExe.Checked && !chkGameFiles.Checked &&
                !chkChromatic.Checked && !chkSharpen.Checked)
            {
                MessageBox.Show("Nothing is selected.", "Nothing to do",
                                MessageBoxButtons.OK, MessageBoxIcon.Information);
                return;
            }
            // The full-width UI step needs the blake3 module. Running under uv
            // supplies it automatically; without uv the user would have to pip
            // install it, so offer the easy route once.
            if (chkGameFiles.Checked && !uvOffered && FindUv() == null)
            {
                uvOffered = true;
                OfferToFetchUv(
                    "The \"Full-width UI\" option needs one extra Python package "
                    + "(blake3), which uv provides automatically.\r\n\r\n"
                    + "Without it that single step is skipped - the camera patch and "
                    + "the display tweaks still apply normally.");
            }

            // The full-width UI step also needs an Oodle decompressor, which
            // cannot be bundled (proprietary). patcher.py can locate one shipped
            // by another UE game or download Epic's Oodle-for-UE build, but only
            // with permission - so ask here and pass the flag through.
            if (chkGameFiles.Checked && !oodleOffered && !OodleAlreadyPresent())
            {
                oodleOffered = true;
                DialogResult d = MessageBox.Show(
                    "The \"Full-width UI\" option needs an Oodle decompressor to read "
                    + "the game's data files. It cannot be bundled with this fix "
                    + "because it is proprietary.\r\n\r\n"
                    + "Allow the patcher to obtain one? It first looks for a copy "
                    + "shipped by another Unreal Engine game on this PC, and only "
                    + "downloads Epic's Oodle-for-UE build (~7 MB) if there is "
                    + "none.\r\n\r\n"
                    + "Choosing No just skips that one step - the camera patch and "
                    + "the display tweaks still apply normally.",
                    "Get Oodle decompressor?", MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question);
                if (d == DialogResult.Yes) fetchOodle = true;
            }
            if (fetchOodle) argv.Add("--fetch-oodle");

            if (!chkExe.Checked) argv.Add("--no-exe");
            if (!chkGameFiles.Checked) argv.Add("--no-game-files");
            if (!chkChromatic.Checked) argv.Add("--no-chromatic-fix");
            if (!chkSharpen.Checked) argv.Add("--no-sharpen");
            Run(argv);
        }

        private void OnRestore(object sender, EventArgs e)
        {
            List<string> argv = new List<string>();
            if (!CommonArgs(argv)) return;
            argv.Add("--restore");
            Run(argv);
        }

        [STAThread]
        public static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }
    }
}
