param(
    [Parameter(Mandatory=$true)][string]$LabDirectory,
    [Parameter(Mandatory=$true)][string]$BaseDisk,
    [switch]$ConfirmCreate
)
$ErrorActionPreference='Stop'
if (-not $ConfirmCreate) { throw 'Explicit -ConfirmCreate required' }
$labRoot=[IO.Path]::GetFullPath($LabDirectory).TrimEnd('\')
$basePath=[IO.Path]::GetFullPath($BaseDisk)
if (-not $basePath.StartsWith($labRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or
    -not (Test-Path -LiteralPath $basePath -PathType Leaf)) { throw 'Base disk must exist inside explicit lab directory' }
$disk=Get-VHD -Path $basePath
if ($disk.VhdFormat -ne 'VHDX' -or $disk.VhdType -ne 'Dynamic' -or $disk.Attached) {
    throw 'Offline dynamic VHDX base required'
}
$switch=Get-VMSwitch -Name 'Default Switch'
if ($switch.SwitchType -ne 'Internal') { throw 'Existing internal Default Switch required; no network reconfiguration allowed' }
foreach ($number in 1,2) {
    $name="cs-s2s6-node$number"
    if (Get-VM -Name $name -ErrorAction SilentlyContinue) { throw "Refusing to replace existing VM $name" }
    if (Test-Path -LiteralPath (Join-Path $labRoot $name)) { throw "New node directory required for $name" }
    if (-not (Test-Path -LiteralPath (Join-Path $labRoot "node$number-seed.iso") -PathType Leaf)) {
        throw 'Both initialization ISO files must already exist'
    }
}
foreach ($number in 1,2) {
    $name="cs-s2s6-node$number"
    $nodePath=Join-Path $labRoot $name
    [void](New-Item -ItemType Directory -Path $nodePath)
    $nodeDisk=Join-Path $nodePath 'system.vhdx'
    [void](New-VHD -Path $nodeDisk -ParentPath $basePath -Differencing)
    [void](New-VM -Name $name -Generation 2 -MemoryStartupBytes 4GB -VHDPath $nodeDisk -Path $nodePath -SwitchName $switch.Name)
    Set-VMProcessor -VMName $name -Count 2
    Set-VMMemory -VMName $name -DynamicMemoryEnabled $false
    Set-VM -Name $name -AutomaticStartAction Nothing -AutomaticStopAction ShutDown -AutomaticCheckpointsEnabled $false
    Set-VMFirmware -VMName $name -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority
    Add-VMDvdDrive -VMName $name -Path (Join-Path $labRoot "node$number-seed.iso")
    Set-VMFirmware -VMName $name -FirstBootDevice (Get-VMHardDiskDrive -VMName $name)
    # No bridge, NAT rules, forwarding, external switch, public listener or
    # modification of existing VMs/switches is performed by this script.
    Start-VM -Name $name
}
Get-VM -Name 'cs-s2s6-node1','cs-s2s6-node2' |
    Select-Object Name,State,Generation,ProcessorCount,MemoryAssigned
