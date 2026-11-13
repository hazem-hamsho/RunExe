from runexe.backend.logger import Logger
from runexe.backend.wine.wineprogram import WineProgram

logging = Logger()


class Hh(WineProgram):
    program = "Wine HTML help viewer"
    command = "hh"
