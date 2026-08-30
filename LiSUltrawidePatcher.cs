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

        // ------------------------------------------------------------------
        // True Hor+ mode (see RESEARCH.md "The Hor+ Breakthrough"):
        // 1) UCameraComponent::GetCameraView: replace the 7-byte
        //    "movzx eax, byte [rbx+2B4]" with "xor eax,eax + nop5" so the
        //    bit-merge clears bConstrainAspectRatio for every camera
        //    (cinematic CineCameras included) -> no pillarbox constraint.
        // 2) FMinimalViewInfo::CalculateProjectionMatrixGivenViewRectangle:
        //    rewrite "cmp dl,2" and "cmp dl,1" immediates to 0xFF so every
        //    perspective camera takes the MaintainYFOV branch, where UE5
        //    derives vFOV from the AUTHORED aspect ratio -> true Hor+.
        // The player camera constant 0x23E665C must remain STOCK (1.7777778)
        // in this mode; only the photo table gets the monitor aspect ratio.
        // Signatures are unique in the binary and act as a fallback if a game
        // update shifts the file offsets.
        // ------------------------------------------------------------------

        private const string SigUnconstrain = "0F B6 83 B4 02 00 00 33 47 4C 83 E0 01";
        private const int ExpUnconstrain = 0x441A14C;
        private static readonly byte[] PatchUnconstrain = new byte[] { 0x31, 0xC0, 0x0F, 0x1F, 0x44, 0x00, 0x00 };

        private const string SigAxisBranch = "3B C1 7E 09 80 FA 02 0F 84 ?? ?? ?? ?? 80 FA 01 0F 84 ?? ?? ?? ??";
        private const int ExpAxisBranch = 0x440ABC0;
        // relative edits: +6 (cmp dl,2 imm) -> FF, +15 (cmp dl,1 imm) -> FF

        private const string SigPhotoTable = "DF 7C DB 3D 55 55 55 3F 39 8E E3 3F";
        private const int ExpPhotoTable = 0x69C8A84; // aspect float at +8

        // UCineCameraComponent constructor: "or byte [rdi+2B4], 1" is
        // bConstrainAspectRatio = true. Rewriting the immediate to 0 makes
        // cinematic cameras (and only them) default to unconstrained.
        private const string SigCineCtor = "80 4F 3A 02 33 C0 80 8F 8A 00 00 00 02 80 8F B4 02 00 00 01";
        private const int ExpCineCtor = 0x40049E9;
        private const int CineCtorImmOffset = 19;

        // UCineCameraComponent::GetCameraView holds the binary's only direct
        // call to UCameraComponent::GetCameraView. We reroute it through an
        // int3-padding code cave that calls the original and then clears
        // bConstrainAspectRatio (bit 0 of DesiredView+0x4C, held in rdi):
        //   sub rsp,28 / call Super / add rsp,28 / and byte [rdi+4C],FE / ret
        private const string SigCineGcvCall = "E8 ?? ?? ?? ?? 4C 8B C7 0F 28 CE 48 8B CB E8";
        private const int ExpCineGcvCall = 0x4005B78;
        private const int CineGcvCallAt = 14;

        private int FindCodeCave(byte[] data, int need)
        {
            // .text bounds from PE headers
            int peOff = BitConverter.ToInt32(data, 0x3C);
            short numSections = BitConverter.ToInt16(data, peOff + 6);
            short optSize = BitConverter.ToInt16(data, peOff + 20);
            int secOff = peOff + 24 + optSize;
            int textLo = -1, textHi = -1;
            for (int s = 0; s < numSections; s++)
            {
                int o = secOff + s * 40;
                if (data[o] == (byte)'.' && data[o+1] == (byte)'t' && data[o+2] == (byte)'e' && data[o+3] == (byte)'x' && data[o+4] == (byte)'t')
                {
                    int rawSize = BitConverter.ToInt32(data, o + 16);
                    int rawPtr = BitConverter.ToInt32(data, o + 20);
                    textLo = rawPtr; textHi = rawPtr + rawSize;
                    break;
                }
            }
            if (textLo < 0) throw new InvalidOperationException(".text section not found.");

            // first int3 run big enough, starting at a run boundary
            for (int i = textLo + 1; i < textHi - need; i++)
            {
                if (data[i] != 0xCC || data[i - 1] == 0xCC) continue;
                int j = i;
                while (j < textHi && data[j] == 0xCC) j++;
                if (j - i >= need) return i;
                i = j;
            }
            throw new InvalidOperationException("No int3 code cave found.");
        }

        // Cave A: aspect-gated unconstrain in UCameraComponent::GetCameraView.
        // Replaces the 7-byte movzx flag-copy preamble with "call cave ; nop2".
        // The cave clears bConstrainAspectRatio (bit 0 of eax) only for cameras
        // whose authored AspectRatio lies in (1.75, 1.8) - the 16:9 cutscene
        // cameras. Exploration/photo cameras (patched to the monitor aspect)
        // and square capture cameras are untouched.
        // Range gate (v4 behavior, final): unconstrain any ~16:9-authored view.
        // The loading side-peek is NOT fixable by a camera gate (loads hold the
        // next cutscene camera behind a 16:9 overlay) - see RESEARCH.md 4g.
        private static readonly byte[] CaveAspectGate = new byte[] {
            0x0F, 0xB6, 0x83, 0xB4, 0x02, 0x00, 0x00,       // movzx eax, byte [rbx+2B4]
            0x8B, 0x8B, 0xB0, 0x02, 0x00, 0x00,             // mov   ecx, [rbx+2B0]
            0x81, 0xF9, 0x00, 0x00, 0xE0, 0x3F,             // cmp   ecx, 1.75f
            0x76, 0x0B,                                     // jbe   done
            0x81, 0xF9, 0x66, 0x66, 0xE6, 0x3F,             // cmp   ecx, 1.8f
            0x73, 0x03,                                     // jae   done
            0x83, 0xE0, 0xFE,                               // and   eax, -2
            0xC3                                            // done: ret
        };

        private void ApplyAspectGateCave(byte[] data)
        {
            int site = LocateSignature(data, SigUnconstrain, ExpUnconstrain, "GetCameraView gate site");
            int cave = FindCodeCave(data, CaveAspectGate.Length + 8);
            CaveAspectGate.CopyTo(data, cave);
            data[site] = 0xE8;
            BitConverter.GetBytes(cave - (site + 5)).CopyTo(data, site + 1);
            data[site + 5] = 0x66; data[site + 6] = 0x90; // 2-byte nop
            Log(string.Format("Patched: aspect-gated unconstrain cave @ 0x{0:X} (site 0x{1:X})", cave, site));
        }

        // Cave B: force bConstrainAspectRatio=TRUE on UCineCameraComponent
        // views (in this game those are the loading/transition cameras, which
        // must stay pillarboxed even though cave A would unconstrain 16:9).
        private void ApplyCineGcvCave(byte[] data)
        {
            int site = LocateSignature(data, SigCineGcvCall, ExpCineGcvCall, "Cine GetCameraView call site");
            int callOff = site + CineGcvCallAt;
            if (data[callOff] != 0xE8)
                throw new InvalidOperationException("Cine GetCameraView call site: expected E8.");
            int oldDisp = BitConverter.ToInt32(data, callOff + 1);
            int superOff = callOff + 5 + oldDisp;

            byte[] prologue = new byte[] { 0x48, 0x83, 0xEC, 0x28 };
            byte[] epilogue = new byte[] { 0x48, 0x83, 0xC4, 0x28, 0x80, 0x4F, 0x4C, 0x01, 0xC3 };
            int cave = FindCodeCave(data, prologue.Length + 5 + epilogue.Length + 8);

            Array.Copy(prologue, 0, data, cave, prologue.Length);
            int caveCall = cave + prologue.Length;
            data[caveCall] = 0xE8;
            BitConverter.GetBytes(superOff - (caveCall + 5)).CopyTo(data, caveCall + 1);
            epilogue.CopyTo(data, caveCall + 5);
            BitConverter.GetBytes(cave - (callOff + 5)).CopyTo(data, callOff + 1);
            Log(string.Format("Patched: cine (loading) views forced 16:9 via cave @ 0x{0:X} (call site 0x{1:X})", cave, callOff));
        }

        private static void ParseSig(string sig, out byte[] pat, out bool[] mask)
        {
            string[] parts = sig.Split(new char[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
            pat = new byte[parts.Length];
            mask = new bool[parts.Length];
            for (int i = 0; i < parts.Length; i++)
            {
                if (parts[i] == "??") { pat[i] = 0; mask[i] = false; }
                else { pat[i] = Convert.ToByte(parts[i], 16); mask[i] = true; }
            }
        }

        private static bool MatchesAt(byte[] data, int off, byte[] pat, bool[] mask)
        {
            if (off < 0 || off + pat.Length > data.Length) return false;
            for (int j = 0; j < pat.Length; j++)
                if (mask[j] && data[off + j] != pat[j]) return false;
            return true;
        }

        // Locate a patch site: prefer the known offset if its bytes still match,
        // otherwise fall back to a unique full signature scan.
        private int LocateSignature(byte[] data, string sig, int expectedOffset, string name)
        {
            byte[] pat; bool[] mask;
            ParseSig(sig, out pat, out mask);
            if (MatchesAt(data, expectedOffset, pat, mask)) return expectedOffset;

            int found = -1;
            for (int i = 0; i <= data.Length - pat.Length; i++)
            {
                if (MatchesAt(data, i, pat, mask))
                {
                    if (found >= 0)
                        throw new InvalidOperationException("Signature for '" + name + "' is ambiguous.");
                    found = i;
                }
            }
            if (found < 0)
                throw new InvalidOperationException("Signature for '" + name + "' not found. Unsupported game version.");
            Log(string.Format("Note: '{0}' moved to file offset 0x{1:X} (game update?).", name, found));
            return found;
        }

        private void ApplyHorPlusPatches(byte[] data, byte[] targetAspectBytes)
        {
            int a = LocateSignature(data, SigUnconstrain, ExpUnconstrain, "Unconstrain cameras");
            Array.Copy(PatchUnconstrain, 0, data, a, PatchUnconstrain.Length);
            Log(string.Format("Patched: Unconstrain cameras (GetCameraView) @ 0x{0:X}", a));

            int b = LocateSignature(data, SigAxisBranch, ExpAxisBranch, "Force MaintainYFOV branch");
            data[b + 6] = 0xFF;   // cmp dl,2 -> cmp dl,0xFF (MajorAxisFOV check dead)
            data[b + 15] = 0xFF;  // cmp dl,1 -> cmp dl,0xFF (MaintainXFOV check dead)
            Log(string.Format("Patched: Force Hor+ MaintainYFOV branch @ 0x{0:X}", b));

            int c = LocateSignature(data, SigPhotoTable, ExpPhotoTable, "Photo projection table");
            Array.Copy(targetAspectBytes, 0, data, c + 8, 4);
            Log(string.Format("Patched: Photo projection table @ 0x{0:X}", c + 8));
        }

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
            cmbCutsceneMode.Items.Add("Recommended: Hor+ Cutscenes + Classic Ultrawide Exploration/Photos/Loading");
            cmbCutsceneMode.Items.Add("True Hor+ Everywhere (experimental; photos may skew, loading pop-in visible)");
            cmbCutsceneMode.Items.Add("Legacy: Uncropped 16:9 Cutscenes (Pillarboxed Cinematics)");
            cmbCutsceneMode.Items.Add("Legacy: Full Ultrawide Cutscenes (Edge-to-Edge with ~20% Lens Crop)");
            cmbCutsceneMode.SelectedIndex = 0;
            cmbCutsceneMode.SelectedIndexChanged += (s, e) => UpdateHexPreview();
            this.Controls.Add(cmbCutsceneMode);

            lblHexResult = new Label();
            lblHexResult.Text = "Aspect Ratio: 2.37037 (Hex: 26 B4 17 40) | Mode: Hor+ Cutscenes + Classic";
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
            string modeStr;
            switch (cmbCutsceneMode.SelectedIndex)
            {
                case 1: modeStr = "True Hor+ Everywhere (experimental)"; break;
                case 2: modeStr = "Legacy Uncropped 16:9 Cutscenes"; break;
                case 3: modeStr = "Legacy Full Ultrawide (Vert-)"; break;
                default: modeStr = "Hor+ Cutscenes + Classic"; break;
            }
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
                int mode = cmbCutsceneMode.SelectedIndex; // 0=hybrid, 1=Hor+ exe-only, 2=legacy clean, 3=legacy full

                Log(string.Format("Starting patch -> Aspect Ratio: {0:F6} ({1}) | Mode index: {2}...",
                    targetRatio, targetHexStr, mode));

                string backupPath = exePath + ".original";
                if (!File.Exists(backupPath))
                {
                    File.Copy(exePath, backupPath, false);
                    Log("Created original backup: " + Path.GetFileName(backupPath));
                }

                // Always read from clean original backup to ensure pristine patch
                byte[] data = File.ReadAllBytes(backupPath);

                if (mode == 0)
                {
                    // Cine mode: exactly the proven classic behavior for
                    // exploration, photos and loading (constrained full-width
                    // player camera + matching photo table), plus Hor+
                    // cutscenes via the cine-only constructor patch.
                    foreach (int off in CleanAspectOffsets)
                        if (off + 4 <= data.Length)
                            Array.Copy(targetBytes, 0, data, off, 4);
                    Log("Patched: player camera + photo table aspect constants (classic behavior).");

                    int b0 = LocateSignature(data, SigAxisBranch, ExpAxisBranch, "Force MaintainYFOV branch");
                    data[b0 + 6] = 0xFF;
                    data[b0 + 15] = 0xFF;
                    Log(string.Format("Patched: Force Hor+ MaintainYFOV branch @ 0x{0:X}", b0));

                    ApplyAspectGateCave(data);
                    ApplyCineGcvCave(data);
                }
                else if (mode == 1)
                {
                    ApplyHorPlusPatches(data, targetBytes);
                    // 0x23E665C intentionally stays stock: the engine's Hor+ math
                    // divides by the authored aspect ratio.
                }
                else
                {
                    int[] offsetsToPatch = mode == 2 ? CleanAspectOffsets : AllAspectOffsets;
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
                }

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

                string summary;
                if (mode == 0)
                    summary = "Cutscenes & dialogues: True Hor+ Ultrawide (0% vertical crop, no bars)\nExploration, photos, loading: classic proven ultrawide behavior";
                else if (mode == 1)
                    summary = "Everything (photo mode included): True Hor+ Ultrawide\n0% vertical crop, no black bars (photos may skew)";
                else if (mode == 2)
                    summary = "Exploration & Photos: Ultrawide\nCutscenes: Uncropped 16:9 (pillarboxed)";
                else
                    summary = "Everything: Full Ultrawide (~20% vertical crop in cinematics)";
                MessageBox.Show(
                    string.Format("Successfully patched to {0:F6} ({1})!\n\n{2}\n\nLaunch the game to play!",
                        targetRatio, targetHexStr, summary),
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
