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

# Set the stage with two terminal comments (now valid in zsh)
Type "# A 14-note sample vault. Watch Basalt find the buried insight."
Sleep 600ms
Enter
Sleep 800ms

# The actual run
Type "basalt demo"
Sleep 700ms
Enter

# Let it work — cold ~3s, warm ~1s. Buffer for safety.
Sleep 5s

# Hold on the result so a reader can absorb
Sleep 6s
