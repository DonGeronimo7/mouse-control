# AppImage build notes

An AppImage can bundle the Python application and user-space Python libraries,
but it cannot safely bundle host udev rules, systemd user services, kernel input
support, hidraw permissions, or OpenRazer's daemon/kernel integration. Build it on an
x86_64 Linux host using an external `appimagetool`, then name the result
`Mouse-Control-0.8.2-x86_64.AppImage`. Users still need host input permissions
and any desired optional backend; `mouse-control doctor` reports missing pieces.
