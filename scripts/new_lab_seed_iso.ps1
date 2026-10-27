param(
    [Parameter(Mandatory=$true)][string]$SourceDirectory,
    [Parameter(Mandatory=$true)][string]$Destination
)
$ErrorActionPreference='Stop'
$sourcePath=[IO.Path]::GetFullPath($SourceDirectory)
$destinationPath=[IO.Path]::GetFullPath($Destination)
if (-not (Test-Path -LiteralPath $sourcePath -PathType Container) -or
    (Test-Path -LiteralPath $destinationPath)) { throw 'Existing source and new destination required' }
# IMAPI is provided by Windows; no unsigned ISO helper download is required.
if (-not ('CustomerServiceLab.IsoWriter' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
namespace CustomerServiceLab {
 public static class IsoWriter {
  public static void Write(string path, object stream, long length) {
   var input=(IStream)stream;
   var buffer=new byte[65536];
   var count=Marshal.AllocHGlobal(sizeof(int));
   try {
    using(var output=new FileStream(path,FileMode.CreateNew,FileAccess.Write)) {
     long copied=0;
     while(copied<length) {
      int wanted=(int)Math.Min(buffer.Length,length-copied);
      input.Read(buffer,wanted,count);
      int read=Marshal.ReadInt32(count);
      if(read<=0 || read>wanted) throw new EndOfStreamException();
      output.Write(buffer,0,read); copied+=read;
     }
     output.Flush(true);
    }
   } finally { Marshal.FreeHGlobal(count); }
  }
 }
}
'@
}
$image=New-Object -ComObject IMAPI2FS.MsftFileSystemImage
try {
    $image.FileSystemsToCreate=3
    $image.VolumeName='cidata'
    $image.Root.AddTree($sourcePath,$false)
    $result=$image.CreateResultImage()
    [CustomerServiceLab.IsoWriter]::Write($destinationPath,$result.ImageStream,
        [long]$result.TotalBlocks*[long]$result.BlockSize)
    Get-Item -LiteralPath $destinationPath | Select-Object FullName,Length
} finally {
    [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($image)
}
