"""
The desktop app (B40): CheapTrip.exe on Windows.

  app.py            window (pywebview on Edge WebView2), tray, start and quit
  engine_thread.py  the engine and the local API on their own event loop
  instance.py       one CheapTrip at a time; a second launch hands over to the first
  links.py          cheaptrip:// links and the app's screen routes
  tray.py           the tray icon: states and menu
  windows.py        Windows specifics (AppUserModelID, message box, WebView2 check)
"""
