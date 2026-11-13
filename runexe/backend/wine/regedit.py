from runexe.backend.logger import Logger
from runexe.backend.wine.wineprogram import WineProgram

logging = Logger()


class Regedit(WineProgram):
    program = "Wine Registry Editor"
    command = "regedit"
