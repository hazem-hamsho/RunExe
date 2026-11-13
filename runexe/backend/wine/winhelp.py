from runexe.backend.logger import Logger
from runexe.backend.wine.wineprogram import WineProgram

logging = Logger()


class WinHelp(WineProgram):
    program = "Microsoft help file viewer"
    command = "winhelp"
