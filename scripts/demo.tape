# Basalt — demo screencast
# Generates docs/demo.gif and docs/demo.mp4
# Run from the repo root:  vhs scripts/demo.tape

Output docs/demo.gif
Output docs/demo.mp4

Set Shell "zsh"
Set FontFamily "JetBrains Mono"
Set FontSize 14
Set Width 1200
Set Height 760
Set Padding 28
Set TypingSpeed 55ms
Set Theme { "name": "basalt", "black": "#0E0D0C", "red": "#D9824B", "green": "#EFE9E2", "yellow": "#D9824B", "blue": "#C9C0B4", "magenta": "#D9824B", "cyan": "#C9C0B4", "white": "#EFE9E2", "brightBlack": "#7A7269", "brightRed": "#D9824B", "brightGreen": "#EFE9E2", "brightYellow": "#D9824B", "brightBlue": "#C9C0B4", "brightMagenta": "#D9824B", "brightCyan": "#C9C0B4", "brightWhite": "#EFE9E2", "background": "#0E0D0C", "foreground": "#EFE9E2", "cursor": "#D9824B", "selection": "#2A2622" }
Set CursorBlink false

# Activate the venv, enable shell comments, clear the buffer (hidden setup)
Hide
Type "cd ~/Projects/basalt && source .venv/bin/activate && setopt INTERACTIVE_COMMENTS && clear" Enter
Sleep 1s
Show

# Set the stage with a plain-English scene-setter
Type "# What did my notes already know?"
Sleep 400ms
Enter
Sleep 500ms

# The actual run
Type "basalt demo"
Sleep 500ms
Enter

# Let it work — cold ~3s, warm ~1s. Add a 1.5s thinking beat before output.
Sleep 1.5s

# Hold on the result + action prompts so a reader can absorb
Sleep 3s
