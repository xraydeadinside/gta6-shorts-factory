' Started by Windows Task Scheduler every 10 minutes ("GTA6 Shorts Helper").
' Runs the YouTube helper invisibly: it asks the bot for tasks and exits in a second if there are none.
Dim fso, shell, root
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
shell.CurrentDirectory = root
If Not fso.FolderExists(root & "\work\helper") Then
  If Not fso.FolderExists(root & "\work") Then fso.CreateFolder(root & "\work")
  fso.CreateFolder(root & "\work\helper")
End If
shell.Run "cmd /c ""C:\Program Files\nodejs\node.exe"" helper\youtube-helper.mjs --auto >> work\helper\auto.log 2>&1", 0, False
