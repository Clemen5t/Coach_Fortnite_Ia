Option Explicit

Dim shell, fso, base, command
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
command = "cmd.exe /c " & Chr(34) & Chr(34) & base & "\Lancer.bat" & Chr(34) & " --silent" & Chr(34)

shell.Run command, 0, False
