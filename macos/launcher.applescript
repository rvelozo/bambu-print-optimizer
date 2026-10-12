-- Slicer Optimizer launcher. Opening the app shows the home page; dropping .3mf files on it,
-- or choosing it in Finder's Open With menu, opens those projects. Both start the local app
-- in the background if it isn't running. make-app.sh fills in the path to slicer-opt.
property launcher : "__SLICER_OPT__"

on run
	do shell script quoted form of launcher & " open --alert > /dev/null 2>&1 &"
end run

on open theFiles
	set args to ""
	repeat with f in theFiles
		set args to args & " " & quoted form of (POSIX path of f)
	end repeat
	do shell script quoted form of launcher & " open --alert" & args & " > /dev/null 2>&1 &"
end open
