using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Windows.Forms;

namespace LiSUltrawidePatcher
{
    public class MainForm : Form
    {
        private Label lblTitle;
        private Label lblExePath;
        private TextBox txtExePath;
        private Button btnBrowse;
        private Label lblPreset;
        private ComboBox cmbPresets;
        private Label lblCustom;
        private TextBox txtWidth;
        private Label lblX;
        private TextBox txtHeight;
        private Label lblHexResult;

        private Label lblCutsceneMode;
        private ComboBox cmbCutsceneMode;

        private Button btnPatch;
        private Button btnRestore;
        private TextBox txtLog;

        private struct ResolutionPreset
        {
            public string Name;
            public int Width;
            public int Height;
            public ResolutionPreset(string name, int w, int h)
            {
                Name = name;
                Width = w;
                Height = h;
            }
            public override string ToString() { return Name; }
        }

        private List<ResolutionPreset> presets = new List<ResolutionPreset>()
        {
            new ResolutionPreset("5120x2160 (21:9 WUHD 4K)", 5120, 2160),
            new ResolutionPreset("3440x1440 (21:9 UWQHD)", 3440, 1440),
            new ResolutionPreset("2560x1080 (21:9 UWD)", 2560, 1080),
            new ResolutionPreset("3840x1600 (24:10 UW)", 3840, 1600),
            new ResolutionPreset("5120x1440 (32:9 Super Ultrawide)", 5120, 1440),
            new ResolutionPreset("3840x1080 (32:9 Super Ultrawide)", 3840, 1080),
            new ResolutionPreset("7680x2160 (32:9 Super Ultrawide)", 7680, 2160),
            new ResolutionPreset("3840x1200 (32:10)", 3840, 1200),
            new ResolutionPreset("2560x1600 (16:10)", 2560, 1600),
            new ResolutionPreset("Custom Resolution...", 0, 0)
        };

        // 11 Verified Camera Aspect Ratio Locations
        private readonly int[] AllAspectOffsets = new int[]
        {
            0x257BDEC, 0x23E5558, 0x23E5739, 0x23E665C, 0x43FEB0F,
            0x43FEB58, 0x43FEFD1, 0x44004BF, 0x440050B, 0x4401BBF, 0x69C8A8C
        };

        // 2-Offset Clean Mode: Player Exploration (0x23E665C) + Photo Table (0x69C8A8C)
        // Keeps cutscenes in pristine uncropped 16:9 (Zero vertical loss)
        private readonly int[] CleanAspectOffsets = new int[]
        {
            0x23E665C, 0x69C8A8C
        };

        public MainForm()
        {
            InitializeComponent();
            AutoDetectExe();
            AutoDetectResolution();
        }

        private void InitializeComponent()
        {
            this.Text = "Life is Strange: Double Exposure - Ultrawide Patcher";
            this.Size = new Size(620, 560);
            this.StartPosition = FormStartPosition.CenterScreen;
            this.FormBorderStyle = FormBorderStyle.FixedSingle;
            this.MaximizeBox = false;
            this.BackColor = Color.FromArgb(24, 26, 32);
            this.ForeColor = Color.White;

            lblTitle = new Label();
            lblTitle.Text = "Life is Strange: Double Exposure - Ultrawide Patcher";
            lblTitle.Font = new Font("Segoe UI", 12.5f, FontStyle.Bold);
            lblTitle.ForeColor = Color.FromArgb(90, 180, 255);
            lblTitle.Location = new Point(20, 15);
            lblTitle.Size = new Size(560, 28);
            this.Controls.Add(lblTitle);

            // Exe path
            lblExePath = new Label();
            lblExePath.Text = "Game Executable (Chronos-Win64-Shipping.exe):";
            lblExePath.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            lblExePath.ForeColor = Color.FromArgb(200, 210, 225);
            lblExePath.Location = new Point(20, 50);
            lblExePath.Size = new Size(400, 18);
            this.Controls.Add(lblExePath);

            txtExePath = new TextBox();
            txtExePath.Font = new Font("Segoe UI", 9f);
            txtExePath.BackColor = Color.FromArgb(35, 38, 48);
            txtExePath.ForeColor = Color.White;
            txtExePath.Location = new Point(20, 72);
            txtExePath.Size = new Size(465, 24);
            this.Controls.Add(txtExePath);

            btnBrowse = new Button();
            btnBrowse.Text = "Browse...";
            btnBrowse.Font = new Font("Segoe UI", 9f);
            btnBrowse.BackColor = Color.FromArgb(50, 55, 70);
            btnBrowse.ForeColor = Color.White;
            btnBrowse.FlatStyle = FlatStyle.Flat;
            btnBrowse.FlatAppearance.BorderSize = 0;
            btnBrowse.Location = new Point(495, 71);
            btnBrowse.Size = new Size(85, 26);
            btnBrowse.Cursor = Cursors.Hand;
            btnBrowse.Click += BtnBrowse_Click;
            this.Controls.Add(btnBrowse);

            // Preset Resolution
            lblPreset = new Label();
            lblPreset.Text = "Target Resolution Preset:";
            lblPreset.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            lblPreset.ForeColor = Color.FromArgb(200, 210, 225);
            lblPreset.Location = new Point(20, 108);
            lblPreset.Size = new Size(200, 18);
            this.Controls.Add(lblPreset);

            cmbPresets = new ComboBox();
            cmbPresets.DropDownStyle = ComboBoxStyle.DropDownList;
            cmbPresets.Font = new Font("Segoe UI", 9.5f);
            cmbPresets.BackColor = Color.FromArgb(35, 38, 48);
            cmbPresets.ForeColor = Color.White;
            cmbPresets.Location = new Point(20, 130);
            cmbPresets.Size = new Size(300, 26);
            foreach (var p in presets) cmbPresets.Items.Add(p);
            cmbPresets.SelectedIndex = 0;
            cmbPresets.SelectedIndexChanged += CmbPresets_SelectedIndexChanged;
            this.Controls.Add(cmbPresets);

            // Custom Resolution Inputs
            lblCustom = new Label();
            lblCustom.Text = "Custom Res:";
            lblCustom.Font = new Font("Segoe UI", 9f);
            lblCustom.ForeColor = Color.FromArgb(170, 180, 195);
            lblCustom.Location = new Point(335, 108);
            lblCustom.Size = new Size(100, 18);
            this.Controls.Add(lblCustom);

            txtWidth = new TextBox();
            txtWidth.Font = new Font("Segoe UI", 9f);
            txtWidth.BackColor = Color.FromArgb(35, 38, 48);
            txtWidth.ForeColor = Color.White;
            txtWidth.Location = new Point(335, 130);
            txtWidth.Size = new Size(95, 24);
            txtWidth.TextChanged += (s, e) => UpdateHexPreview();
            this.Controls.Add(txtWidth);

            lblX = new Label();
            lblX.Text = "×";
            lblX.Font = new Font("Segoe UI", 12f, FontStyle.Bold);
            lblX.ForeColor = Color.FromArgb(170, 180, 195);
            lblX.Location = new Point(435, 128);
            lblX.Size = new Size(20, 24);
            this.Controls.Add(lblX);

            txtHeight = new TextBox();
            txtHeight.Font = new Font("Segoe UI", 9f);
            txtHeight.BackColor = Color.FromArgb(35, 38, 48);
            txtHeight.ForeColor = Color.White;
            txtHeight.Location = new Point(458, 130);
            txtHeight.Size = new Size(95, 24);
            txtHeight.TextChanged += (s, e) => UpdateHexPreview();
            this.Controls.Add(txtHeight);

            // Cutscene Framing Mode
            lblCutsceneMode = new Label();
            lblCutsceneMode.Text = "Cutscene & Dialogue Framing Mode:";
            lblCutsceneMode.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            lblCutsceneMode.ForeColor = Color.FromArgb(200, 210, 225);
            lblCutsceneMode.Location = new Point(20, 166);
            lblCutsceneMode.Size = new Size(300, 18);
            this.Controls.Add(lblCutsceneMode);

            cmbCutsceneMode = new ComboBox();
            cmbCutsceneMode.DropDownStyle = ComboBoxStyle.DropDownList;
            cmbCutsceneMode.Font = new Font("Segoe UI", 9.5f);
            cmbCutsceneMode.BackColor = Color.FromArgb(35, 38, 48);
            cmbCutsceneMode.ForeColor = Color.White;
            cmbCutsceneMode.Location = new Point(20, 188);
            cmbCutsceneMode.Size = new Size(533, 26);
            cmbCutsceneMode.Items.Add("Recommended: Uncropped 16:9 Cutscenes (0% Vertical Crop / Full Headroom)");
            cmbCutsceneMode.Items.Add("Full Ultrawide Cutscenes (Edge-to-Edge with ~20% Lens Crop)");
            cmbCutsceneMode.SelectedIndex = 0;
            cmbCutsceneMode.SelectedIndexChanged += (s, e) => UpdateHexPreview();
            this.Controls.Add(cmbCutsceneMode);

            lblHexResult = new Label();
            lblHexResult.Text = "Aspect Ratio: 2.37037 (Hex: 26 B4 17 40) | Mode: Uncropped 16:9 Cutscenes";
            lblHexResult.Font = new Font("Consolas", 9f, FontStyle.Bold);
            lblHexResult.ForeColor = Color.FromArgb(255, 190, 80);
            lblHexResult.Location = new Point(20, 222);
            lblHexResult.Size = new Size(560, 20);
            this.Controls.Add(lblHexResult);

            // Action Buttons
            btnPatch = new Button();
            btnPatch.Text = "Patch Game Executable";
            btnPatch.Font = new Font("Segoe UI", 10.5f, FontStyle.Bold);
            btnPatch.BackColor = Color.FromArgb(40, 140, 70);
            btnPatch.ForeColor = Color.White;
            btnPatch.FlatStyle = FlatStyle.Flat;
            btnPatch.FlatAppearance.BorderSize = 0;
            btnPatch.Location = new Point(20, 250);
            btnPatch.Size = new Size(350, 36);
            btnPatch.Cursor = Cursors.Hand;
            btnPatch.Click += BtnPatch_Click;
            this.Controls.Add(btnPatch);

            btnRestore = new Button();
            btnRestore.Text = "Restore Original Stock (16:9)";
            btnRestore.Font = new Font("Segoe UI", 9.5f);
            btnRestore.BackColor = Color.FromArgb(60, 65, 80);
            btnRestore.ForeColor = Color.White;
            btnRestore.FlatStyle = FlatStyle.Flat;
            btnRestore.FlatAppearance.BorderSize = 0;
            btnRestore.Location = new Point(380, 250);
            btnRestore.Size = new Size(200, 36);
            btnRestore.Cursor = Cursors.Hand;
            btnRestore.Click += BtnRestore_Click;
            this.Controls.Add(btnRestore);

            // Log Console
            txtLog = new TextBox();
            txtLog.Multiline = true;
            txtLog.ReadOnly = true;
            txtLog.ScrollBars = ScrollBars.Vertical;
            txtLog.Font = new Font("Consolas", 9f);
            txtLog.BackColor = Color.FromArgb(16, 18, 22);
            txtLog.ForeColor = Color.FromArgb(180, 220, 200);
            txtLog.Location = new Point(20, 298);
            txtLog.Size = new Size(560, 200);
            this.Controls.Add(txtLog);
        }

        private void AutoDetectExe()
        {
            string[] searchPaths = new string[]
            {
                "Chronos-Win64-Shipping.exe",
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Chronos-Win64-Shipping.exe"),
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "Chronos", "Binaries", "Win64", "Chronos-Win64-Shipping.exe"),
                Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "Chronos", "Binaries", "Win64", "Chronos-Win64-Shipping.exe"),
                @"d:\SteamLibrary\steamapps\common\LifeIsStrangeDoubleExposure\Chronos\Binaries\Win64\Chronos-Win64-Shipping.exe",
                @"C:\Program Files (x86)\Steam\steamapps\common\LifeIsStrangeDoubleExposure\Chronos\Binaries\Win64\Chronos-Win64-Shipping.exe"
            };

            foreach (string p in searchPaths)
            {
                if (File.Exists(p))
                {
                    txtExePath.Text = Path.GetFullPath(p);
                    Log("Auto-detected game binary: " + txtExePath.Text);
                    return;
                }
            }
            Log("Executable not found automatically. Please click 'Browse...' to locate Chronos-Win64-Shipping.exe.");
        }

        private void AutoDetectResolution()
        {
            int screenWidth = Screen.PrimaryScreen.Bounds.Width;
            int screenHeight = Screen.PrimaryScreen.Bounds.Height;

            Log(string.Format("Primary Display Resolution Detected: {0}x{1}", screenWidth, screenHeight));

            for (int i = 0; i < presets.Count; i++)
            {
                if (presets[i].Width == screenWidth && presets[i].Height == screenHeight)
                {
                    cmbPresets.SelectedIndex = i;
                    return;
                }
            }

            cmbPresets.SelectedIndex = presets.Count - 1; // Custom
            txtWidth.Text = screenWidth.ToString();
            txtHeight.Text = screenHeight.ToString();
        }

        private void CmbPresets_SelectedIndexChanged(object sender, EventArgs e)
        {
            var sel = (ResolutionPreset)cmbPresets.SelectedItem;
            if (sel.Width > 0 && sel.Height > 0)
            {
                txtWidth.Text = sel.Width.ToString();
                txtHeight.Text = sel.Height.ToString();
                txtWidth.Enabled = false;
                txtHeight.Enabled = false;
            }
            else
            {
                txtWidth.Enabled = true;
                txtHeight.Enabled = true;
            }
            UpdateHexPreview();
        }

        private byte[] GetTargetHexBytes(out float ratio)
        {
            ratio = 1.7777778f;
            int w, h;
            if (int.TryParse(txtWidth.Text.Trim(), out w) && int.TryParse(txtHeight.Text.Trim(), out h) && w > 0 && h > 0)
            {
                ratio = (float)w / (float)h;
            }
            return BitConverter.GetBytes(ratio);
        }

        private void UpdateHexPreview()
        {
            float ratio;
            byte[] bytes = GetTargetHexBytes(out ratio);
            string hexStr = BitConverter.ToString(bytes).Replace("-", " ");
            string modeStr = cmbCutsceneMode.SelectedIndex == 0 ? "Uncropped 16:9 Cutscenes" : "Full Ultrawide Cutscenes";
            lblHexResult.Text = string.Format("Aspect Ratio: {0:F6} ({1}) | Mode: {2}", ratio, hexStr, modeStr);
        }

        private void BtnBrowse_Click(object sender, EventArgs e)
        {
            using (OpenFileDialog ofd = new OpenFileDialog())
            {
                ofd.Filter = "Unreal Executable (Chronos-Win64-Shipping.exe)|Chronos-Win64-Shipping.exe|All Executables (*.exe)|*.exe";
                if (ofd.ShowDialog() == DialogResult.OK)
                {
                    txtExePath.Text = ofd.FileName;
                    Log("Selected: " + ofd.FileName);
                }
            }
        }

        private void BtnPatch_Click(object sender, EventArgs e)
        {
            string exePath = txtExePath.Text.Trim();
            if (!File.Exists(exePath))
            {
                MessageBox.Show("Please select a valid Chronos-Win64-Shipping.exe file!", "File Not Found", MessageBoxButtons.OK, MessageBoxIcon.Error);
                return;
            }

            try
            {
                float targetRatio;
                byte[] targetBytes = GetTargetHexBytes(out targetRatio);
                string targetHexStr = BitConverter.ToString(targetBytes).Replace("-", " ");
                bool isCleanMode = cmbCutsceneMode.SelectedIndex == 0;

                Log(string.Format("Starting patch -> Aspect Ratio: {0:F6} ({1}) | Cutscene Mode: {2}...",
                    targetRatio, targetHexStr, isCleanMode ? "Uncropped 16:9" : "Full Ultrawide"));

                string backupPath = exePath + ".original";
                if (!File.Exists(backupPath))
                {
                    File.Copy(exePath, backupPath, false);
                    Log("Created original backup: " + Path.GetFileName(backupPath));
                }

                // Always read from clean original backup to ensure pristine patch
                byte[] data = File.ReadAllBytes(backupPath);

                int[] offsetsToPatch = isCleanMode ? CleanAspectOffsets : AllAspectOffsets;
                int aspectPatched = 0;
                foreach (int off in offsetsToPatch)
                {
                    if (off + 4 <= data.Length)
                    {
                        Array.Copy(targetBytes, 0, data, off, 4);
                        aspectPatched++;
                    }
                }
                Log(string.Format("Patched {0} Aspect Ratio locations successfully.", aspectPatched));

                File.WriteAllBytes(exePath, data);
                Log("SUCCESS: Updated " + Path.GetFileName(exePath));

                // Disable conflicting SUWSF.ini if present
                string iniPath = Path.Combine(Path.GetDirectoryName(exePath), "SUWSF.ini");
                if (File.Exists(iniPath))
                {
                    string iniContent = File.ReadAllText(iniPath);
                    if (iniContent.Contains("Enabled=true"))
                    {
                        File.WriteAllText(iniPath, iniContent.Replace("Enabled=true", "Enabled=false"));
                        Log("Disabled conflicting SUWSF.ini patch.");
                    }
                }

                MessageBox.Show(
                    string.Format("Successfully patched to {0:F6}!\n\nExploration & Photos: Ultrawide ({1})\nCutscenes: {2}\n\nLaunch the game to play!",
                        targetRatio, targetHexStr, isCleanMode ? "Uncropped 16:9 (0% Vertical Crop)" : "Full Ultrawide"),
                    "Patch Successful",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                Log("ERROR: " + ex.Message);
                MessageBox.Show("An error occurred during patching:\n" + ex.Message, "Patch Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void BtnRestore_Click(object sender, EventArgs e)
        {
            string exePath = txtExePath.Text.Trim();
            string backupPath = exePath + ".original";

            if (!File.Exists(backupPath))
            {
                MessageBox.Show("Original backup (.original) not found. Cannot restore.", "Restore Error", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            try
            {
                File.Copy(backupPath, exePath, true);
                Log("SUCCESS: Restored pristine original game executable.");
                MessageBox.Show("Successfully restored original unmodified game executable!", "Restored", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
            catch (Exception ex)
            {
                Log("ERROR during restore: " + ex.Message);
                MessageBox.Show("Error restoring backup:\n" + ex.Message, "Restore Error", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }

        private void Log(string msg)
        {
            string line = string.Format("[{0:HH:mm:ss}] {1}", DateTime.Now, msg);
            txtLog.AppendText(line + Environment.NewLine);
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
