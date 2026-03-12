Add-Type -AssemblyName System.Windows.Forms, System.Drawing

# --- Initialization ---
$InstallerVersion = "1.0.0"
$AppName = "Vivek Bot"
$DefaultInstallPath = Join-Path $env:LOCALAPPDATA "VivekBot"
$SourceDir = $PSScriptRoot
$PackageDir = Join-Path $PSScriptRoot "app"

# --- GUI - Main Form ---
$form = New-Object Windows.Forms.Form
$form.Text = "Setup - $AppName"
$form.Size = New-Object Drawing.Size(500, 380)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

# --- Header Section ---
$header = New-Object Windows.Forms.Label
$header.Text = "Welcome to the $AppName Setup Wizard"
$header.Font = New-Object Drawing.Font("Segoe UI", 14, [Drawing.FontStyle]::Bold)
$header.Location = New-Object Drawing.Point(20, 20)
$header.AutoSize = $true
$form.Controls.Add($header)

$subHeader = New-Object Windows.Forms.Label
$subHeader.Text = "This wizard will install $AppName on your computer."
$subHeader.Location = New-Object Drawing.Point(24, 60)
$subHeader.Size = New-Object Drawing.Size(440, 40)
$form.Controls.Add($subHeader)

# --- Path Selection ---
$lblPath = New-Object Windows.Forms.Label
$lblPath.Text = "Install Location:"
$lblPath.Location = New-Object Drawing.Point(24, 120)
$lblPath.AutoSize = $true
$form.Controls.Add($lblPath)

$txtPath = New-Object Windows.Forms.TextBox
$txtPath.Text = $DefaultInstallPath
$txtPath.Location = New-Object Drawing.Point(24, 140)
$txtPath.Size = New-Object Drawing.Size(340, 25)
$form.Controls.Add($txtPath)

$btnBrowse = New-Object Windows.Forms.Button
$btnBrowse.Text = "Browse..."
$btnBrowse.Location = New-Object Drawing.Point(375, 138)
$btnBrowse.Size = New-Object Drawing.Size(80, 28)
$btnBrowse.Add_Click({
        $fbd = New-Object Windows.Forms.FolderBrowserDialog
        $fbd.SelectedPath = $txtPath.Text
        if ($fbd.ShowDialog() -eq "OK") { $txtPath.Text = $fbd.SelectedPath }
    })
$form.Controls.Add($btnBrowse)

# --- Progress Bar (Hidden initially) ---
$progressBar = New-Object Windows.Forms.ProgressBar
$progressBar.Location = New-Object Drawing.Point(24, 200)
$progressBar.Size = New-Object Drawing.Size(430, 25)
$progressBar.Style = "Continuous"
$progressBar.Visible = $false
$form.Controls.Add($progressBar)

$lblStatus = New-Object Windows.Forms.Label
$lblStatus.Text = "Ready to install."
$lblStatus.Location = New-Object Drawing.Point(24, 230)
$lblStatus.Size = New-Object Drawing.Size(430, 40)
$lblStatus.Visible = $false
$form.Controls.Add($lblStatus)

# --- Buttons ---
$btnInstall = New-Object Windows.Forms.Button
$btnInstall.Text = "Install"
$btnInstall.Location = New-Object Drawing.Point(280, 300)
$btnInstall.Size = New-Object Drawing.Size(90, 32)
$btnInstall.Font = New-Object Drawing.Font("Segoe UI", 9, [Drawing.FontStyle]::Bold)

$btnClose = New-Object Windows.Forms.Button
$btnClose.Text = "Cancel"
$btnClose.Location = New-Object Drawing.Point(380, 300)
$btnClose.Size = New-Object Drawing.Size(80, 32)
$btnClose.Add_Click({ $form.Close() })

$form.Controls.Add($btnInstall)
$form.Controls.Add($btnClose)

# --- Installation Logic ---
$btnInstall.Add_Click({
        $btnInstall.Enabled = $false
        $btnBrowse.Enabled = $false
        $txtPath.ReadOnly = $true
        $progressBar.Visible = $true
        $lblStatus.Visible = $true
        $btnClose.Text = "Exit"
    
        $DestPath = $txtPath.Text
    
        # Run installation in background (simplified for prototype, normally use job or thread)
        try {
            $lblStatus.Text = "Creating directories..."
            New-Item -ItemType Directory -Force -Path $DestPath
            $progressBar.Value = 10
        
            $lblStatus.Text = "Copying application files..."
            if (-not (Test-Path $PackageDir)) {
                throw "Packaged application folder 'app' not found. Build the executable distribution first."
            }
            Copy-Item -Path "$PackageDir\*" -Destination $DestPath -Recurse -Force
            $progressBar.Value = 30

            $progressBar.Value = 90
        
            $lblStatus.Text = "Creating shortcuts..."
            $Shell = New-Object -ComObject WScript.Shell
            $Shortcut = $Shell.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\$AppName.lnk")
            $Shortcut.TargetPath = Join-Path $DestPath "$AppName.exe"
            $Shortcut.WorkingDirectory = $DestPath
            $Shortcut.Save()
        
            $progressBar.Value = 100
            $lblStatus.Text = "Installation Successful! You can launch the bot from your Desktop."
            $btnInstall.Text = "Finished"
        }
        catch {
            [Windows.Forms.MessageBox]::Show("Installation failed: $($_.Exception.Message)", "Error")
            $lblStatus.Text = "Error occurred."
            $btnInstall.Enabled = $true
        }
    })

$form.ShowDialog()
