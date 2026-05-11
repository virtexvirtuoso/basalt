# Basalt — Connection demo (Verb #3)
# Generates docs/connection.gif and docs/connection.mp4
# Run from repo root: vhs scripts/connection.tape

Output docs/connection.gif
Output docs/connection.mp4

Set Shell "zsh"
Set FontFamily "JetBrains Mono"
Set FontSize 16
Set Framerate 60
Set Width 1200
Set Height 760
Set Padding 28
Set TypingSpeed 35ms
Set Theme { "name": "basalt", "black": "#0E0D0C", "red": "#D9824B", "green": "#EFE9E2", "yellow": "#D9824B", "blue": "#C9C0B4", "magenta": "#D9824B", "cyan": "#C9C0B4", "white": "#EFE9E2", "brightBlack": "#7A7269", "brightRed": "#D9824B", "brightGreen": "#EFE9E2", "brightYellow": "#D9824B", "brightBlue": "#C9C0B4", "brightMagenta": "#D9824B", "brightCyan": "#C9C0B4", "brightWhite": "#EFE9E2", "background": "#0E0D0C", "foreground": "#EFE9E2", "cursor": "#D9824B", "selection": "#2A2622" }
Set CursorBlink false

# Hidden setup
Hide
Type "cd ~/Projects/basalt && source .venv/bin/activate && setopt INTERACTIVE_COMMENTS && clear" Enter
Sleep 1s
Show

# Scene-setter
Type "# The two ideas in different folders that turn out to be the same idea"
Sleep 400ms
Enter
Sleep 500ms

# The command
Type "basalt brief --section connection"
Sleep 500ms
Enter

# Processing beat
Sleep 1.5s

# Show the result
Type ""
Sleep 2s

# Hold for absorption
Sleep 1.5s
