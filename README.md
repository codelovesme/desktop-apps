# codelovesme desktop apps

codelovesme's web applications, each in a Linux desktop window of its own:
**To Do** and **Home**. The window shows the same application as
https://apps.codeloves.me, and keeps to it — its own pages and signing in;
any other link opens in your browser.

## Install

On Linux x86_64 (Debian or Ubuntu), install
[CDLVSM](https://github.com/codelovesme/cdlvsm), then:

```sh
cdlvsm install todo     # To Do
cdlvsm install home     # Home
cdlvsm todo             # or find it in the applications menu
```

Each is its own package, with its own entry in the applications menu.
CDLVSM installs WebKitGTK (`python3-gi`, `gir1.2-webkit2-4.1`) if it is
missing. `cdlvsm upgrade todo` and `cdlvsm uninstall todo` manage it.

## Use

Sign in in the window, with your codelovesme account. You stay signed in
when the window is closed and opened again; each application keeps its own
session, apart from your browser's. Signing in with Google continues in
your browser: approve the window there and it is signed in.

The camera and microphone (To Do's photo and voice capture) are asked for
once: a yes is remembered (in `~/.local/share/codelovesme/<app>/permissions.json`;
delete it to be asked again), a no only until the window closes. Downloads go to your Downloads folder.

## How it works

`window/app_window.py` is one WebKitGTK window shared by every
application; `apps/<app>/app.conf` says which application it is. The window
opens `/desktop/<app>` on the host, where the shell shows that application
alone. There is no bridge between the page and the computer: the
application talks to its server exactly as it does in a browser. (Mike's
desktop app has a device gateway because Mike's server sends work *to* the
computer; these windows need none.)

## Develop

```sh
/usr/bin/python3 -m unittest discover -s tests
tools/package.py todo dev dist         # dist/todo-dev-x86_64-linux.tar.gz
CODELOVESME_HOST_URL=http://127.0.0.1:8923 /usr/bin/python3 window/app_window.py --config apps/todo/app.conf
```

`tools/live-smoke.py` opens the real window against a running host, signs
in through it and saves a picture (see its header). Adding an application
is a new `apps/<app>/` with `app.conf` and an icon, a line in the release
workflow, and a package entry in CDLVSM.
