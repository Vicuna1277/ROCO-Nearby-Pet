Option Explicit
Dim shell, fso, folder, entry, candidates, executable, launched, command, result
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = folder
entry = Chr(34) & fso.BuildPath(folder, "launcher.pyw") & Chr(34)
candidates = Array("pyw.exe -3 ", "pythonw.exe ", "py.exe -3 ", "python.exe ")
launched = False
For Each executable In candidates
    command = executable & entry
    On Error Resume Next
    Err.Clear
    result = shell.Run(command, 0, False)
    If Err.Number = 0 Then launched = True
    On Error GoTo 0
    If launched Then Exit For
Next
If Not launched Then
    MsgBox "Python 3.10+ was not found. Install Python and run the dependency installer.", 16, "RoCo"
End If
