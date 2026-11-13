from runexe.backend.logger import Logger
from runexe.backend.wine.wineprogram import WineProgram

logging = Logger()


class Progman(WineProgram):
    program = "Wine Program Manager"
    command = "progman"
