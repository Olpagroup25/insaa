# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)


def migrate(cr, version):
    # R-01 (reunión 14/09): el estado Borrador de la hoja de ruta
    # desaparece. Una hoja que estaba en borrador pasa a "En reparto"; lo
    # que el borrador bloqueaba (visitas sin control de salida aprobado)
    # ahora lo bloquea control_salida_aprobado.
    cr.execute("UPDATE fsm_route_dayroute SET state = 'en_reparto' WHERE state = 'borrador'")

    # Piso de FUE/FUC (ver res.partner.fue_legado): se guarda la fecha
    # que hoy tiene cada cliente — la que escribió la migración de Water
    # Service — antes de que el cómputo nuevo (solo hojas cerradas) la
    # pueda recalcular sin el histórico. Si además está el snapshot de la
    # migración de contactos (x_insa_fue_ws, campo manual), se toma la más
    # reciente de las dos.
    cr.execute("ALTER TABLE res_partner ADD COLUMN IF NOT EXISTS fue_legado date")
    cr.execute("ALTER TABLE res_partner ADD COLUMN IF NOT EXISTS fuc_legado date")
    cr.execute(
        """
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'res_partner' AND column_name = 'x_insa_fue_ws'
        """
    )
    if cr.fetchone():
        cr.execute(
            """
            UPDATE res_partner
               SET fue_legado = GREATEST(fecha_ultima_entrega, x_insa_fue_ws)
             WHERE fecha_ultima_entrega IS NOT NULL OR x_insa_fue_ws IS NOT NULL
            """
        )
    else:
        cr.execute(
            """
            UPDATE res_partner SET fue_legado = fecha_ultima_entrega
             WHERE fecha_ultima_entrega IS NOT NULL
            """
        )
    cr.execute(
        """
        UPDATE res_partner SET fuc_legado = fecha_ultima_cobranza
         WHERE fecha_ultima_cobranza IS NOT NULL
        """
    )
