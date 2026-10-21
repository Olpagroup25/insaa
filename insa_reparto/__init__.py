from . import models
from . import controllers


def _renombrar_secuencia_hoja_ruta(env):
    """INSA identifica cada hoja de ruta como "REP/2026/0001" (doc D1,
    §7.1), no como el "SR001" que trae fieldservice_route por defecto.

    Esto se hace acá por ORM y no como dato XML (data/ir_sequence.xml se
    intentó primero y no funciona): el ir.model.data de esa secuencia ya
    viene con noupdate=True desde el módulo OCA que la creó, y ese flag
    persiste en la base para siempre — ningún <record> de datos, venga
    de este módulo o de cualquier otro, la puede sobrescribir. Un
    write() directo por ORM no tiene ese problema porque noupdate solo
    aplica al mecanismo de carga de XML, no a la escritura normal."""
    seq = env.ref("fieldservice_route.seq_fsm_route", raise_if_not_found=False)
    if seq:
        seq.write({"prefix": "REP/%(year)s/", "padding": 4})
