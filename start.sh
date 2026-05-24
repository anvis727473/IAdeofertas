#!/bin/bash
# Inicia o servidor web em background e o bot em foreground
python3 web_server.py &
python3 bot.py
