# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
from odoo import fields, models


class FSMRoute(models.Model):
    """La ruta/móvil persistente ("Reparto 01"..."Reparto 17"). Vive acá
    y no en fsm.route.dayroute porque el número identifica al móvil/ruta,
    no al día concreto — lo confirma la reunión ("distribuidor número 9")
    y es la base del login de la app de reparto (D3), ver
    insa.repartidor.credencial."""

    _inherit = "fsm.route"

    numero_reparto = fields.Integer(
        string="N° de reparto",
        help="El \"Reparto 1\", \"Reparto 2\" que usa INSA a diario "
        "(~17 rutas). Determina el login de la app: "
        "\"reparto01@insareparto.local\".",
    )

    # -- C-11: datos base de planificación, por móvil -----------------
    # "No estaría de más, siempre y cuando se pueda medir bien de alguna
    # manera en cuanto a las ubicaciones, las distancias." Son datos del
    # MÓVIL (fijos, se cargan una vez), no de cada hoja de ruta —
    # fsm.route.dayroute los toma de acá como valor por defecto al
    # crearse (ver default_get en ese modelo) reusando los campos
    # nativos de OCA (start_location_id/end_location_id/work_time) en
    # vez de duplicarlos.
    #
    # V-14 (todavía abierto): "Ana enviará un formulario de relevamiento
    # para que INSA complete los datos base de cada móvil." Estos campos
    # quedan vacíos/en 0 hasta que vuelva ese formulario — la pestaña
    # Planificación de la hoja de ruta queda informativa hasta entonces,
    # tal como se acordó en la reunión.
    ubicacion_inicio_id = fields.Many2one(
        comodel_name="fsm.location",
        string="Ubicación de inicio",
        help="Dónde empieza el repartidor ('acá en mi barrio, en mi "
        "cuadra de Córdoba'). Pendiente del formulario de relevamiento "
        "(V-14).",
    )
    ubicacion_fin_id = fields.Many2one(
        comodel_name="fsm.location", string="Ubicación de fin"
    )
    duracion_prevista = fields.Float(
        string="Duración prevista (hs)",
        help="Duración esperada de la jornada para este móvil. Pendiente "
        "del formulario de relevamiento (V-14).",
    )
    km_estimados = fields.Float(string="Km estimados")

    # 4.5 (revisión 29/09): repartidor y vehículo habituales del móvil —
    # se cargan una vez acá y se copian a cada hoja de ruta nueva (ver
    # create() en fsm.route.dayroute). Pueden diferir de lo que termine
    # cargado en una hoja puntual (reemplazos), igual que repartidor_id
    # ya complementaba a person_id desde el 4.1.
    repartidor_habitual_id = fields.Many2one(
        comodel_name="hr.employee", string="Repartidor habitual"
    )
    vehiculo_habitual_id = fields.Many2one(
        comodel_name="fleet.vehicle", string="Vehículo habitual"
    )

    _sql_constraints = [
        (
            "numero_reparto_unique",
            "unique(numero_reparto)",
            "Ya existe una ruta con ese número de reparto.",
        ),
    ]
