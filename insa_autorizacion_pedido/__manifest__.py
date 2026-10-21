# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
{
    "name": "INSA - Autorización de pedidos",
    "version": "18.0.1.15.1",
    "license": "AGPL-3",
    "author": "Olpa Group",
    "summary": "Quién puede pedir para quién: personas autorizadas por cuenta, "
    "y pedido a nombre propio con dirección de entrega dedicada.",
    "depends": [
        "insa_reparto",
        "fieldservice",
        "crm",
        "base_geolocalize",
        "account",
        "l10n_ar",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/insa_autorizacion_pedido_views.xml",
        "wizards/activar_cliente_propio_views.xml",
        "views/res_partner_views.xml",
        "views/insa_pedido_extra_views.xml",
    ],
    "installable": True,
}
