' Launches the given .bat with a fully hidden window.
' Used by Task Scheduler so the server doesn't pop a console / steal focus.
If WScript.Arguments.Count < 1 Then WScript.Quit 1
CreateObject("WScript.Shell").Run """" & WScript.Arguments(0) & """", 0, False
