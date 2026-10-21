# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""API REST para la app de reparto (D3).

El handshake de autenticación (login + verificación de token) más el
primer endpoint de datos: la hoja de ruta del día (/insa/api/hoja-de-ruta).
El resto de los endpoints de datos (control de playa, registrar visita,
etc.) siguen fuera de este alcance; se agregan reusando
_require_credencial() de acá.

auth="public": no hay usuario Odoo detrás. El aislamiento de datos es
responsabilidad de cada endpoint, resolviendo siempre el route_id desde
la credencial verificada del token — nunca desde lo que mande el cliente.
"""
import json
from datetime import date

from odoo import fields, http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request


class InsaRepartoAPIController(http.Controller):

    def _json_response(self, data, status=200):
        return request.make_json_response(data, status=status)

    def _get_body(self):
        try:
            return json.loads(request.httprequest.data or b"{}")
        except ValueError:
            return {}

    def _require_credencial(self):
        """Devuelve la insa.repartidor.credencial del token Bearer, o
        None si no hay token o es inválido/expirado/revocado."""
        auth_header = request.httprequest.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        token = auth_header[len("Bearer "):]
        Cred = request.env["insa.repartidor.credencial"].sudo()
        try:
            return Cred.verify_token(token)
        except UserError:
            return None

    @http.route(
        "/insa/api/login", type="http", auth="public", methods=["POST"], csrf=False
    )
    def login(self, **kw):
        body = self._get_body()
        Cred = request.env["insa.repartidor.credencial"].sudo()
        try:
            cred = Cred._authenticate(body.get("login"), body.get("password"))
        except UserError as e:
            return self._json_response({"error": str(e)}, status=401)
        token = cred.generate_token()
        return self._json_response(
            {
                "token": token,
                "expira_en_horas": 24,
                "reparto": {
                    "route_id": cred.route_id.id,
                    "numero_reparto": cred.route_id.numero_reparto,
                    "login": cred.login,
                },
            }
        )

    @http.route(
        "/insa/api/whoami", type="http", auth="public", methods=["GET"], csrf=False
    )
    def whoami(self, **kw):
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)
        return self._json_response(
            {
                "route_id": cred.route_id.id,
                "numero_reparto": cred.route_id.numero_reparto,
                "login": cred.login,
                "last_login": cred.last_login.isoformat() if cred.last_login else None,
                # Paso 1.16 / supuesto 0.6: Dispensers/Repuestos son D2,
                # fuera de alcance de D1 (README del módulo). Apagado por
                # defecto — la app usa esto para decidir si muestra esas
                # tabs en ControlDePlayaDialog (Paso 2.12).
                "feature_dispensers_repuestos": (
                    request.env["ir.config_parameter"]
                    .sudo()
                    .get_param("insa_reparto.feature_dispensers_repuestos_d1", "0")
                    == "1"
                ),
            }
        )

    @http.route(
        "/insa/api/catalogo",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def catalogo(self, **kw):
        """Catálogo de productos elegibles para control de playa — mismo
        domain que ya usa insa.control.playa.linea.product_id
        (visible_control_playa=True). Faltaba: el doc D1 lista "catálogo
        de artículos" como parte del payload esperado, y el selector de
        artículos de ControlDePlayaDialog (Paso 2.2) no tiene otra forma
        de saber qué productos existen."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        productos = (
            request.env["product.template"]
            .sudo()
            .search([("visible_control_playa", "=", True)])
        )
        return self._json_response(
            {
                "productos": [
                    {
                        "product_id": p.id,
                        "nombre": p.name,
                        "es_retornable": p.es_retornable,
                        "es_envase": p.es_envase,
                        "precio": p.list_price or 0.0,
                    }
                    for p in productos
                ]
            }
        )

    @http.route(
        "/insa/api/clientes/buscar",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def buscar_clientes(self, **kw):
        """Paso 2.6 (Buscador modo 2): busca clientes de reparto por
        nombre O dirección (C-21: "buscar por nombre o dirección"), sin
        importar si están en la hoja de ruta de hoy — a diferencia de
        GET /insa/api/hoja-de-ruta, que siempre está anclado a
        hoja.order_ids del día. `?q=texto`, mínimo 2 caracteres."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        q = (kw.get("q") or "").strip()
        if len(q) < 2:
            return self._json_response({"clientes": []})

        # customer_rank > 0, no type == "fsm_location": verificado contra
        # datos reales (Docker) que los clientes de reparto son partners
        # type='contact' con customer_rank=1 — "fsm_location" es el tipo
        # que OCA fieldservice usa para el partner de la *ubicación*, no
        # del cliente. Ver hallazgo del Paso 2.6 (res_partner.py y el cron
        # de FUE del Paso 1.12 puede que tengan el mismo problema).
        partners = (
            request.env["res.partner"]
            .sudo()
            .search(
                [
                    ("customer_rank", ">", 0),
                    "|",
                    "|",
                    ("name", "ilike", q),
                    ("street", "ilike", q),
                    ("insa_city_id.name", "ilike", q),
                ],
                limit=20,
            )
        )
        return self._json_response(
            {
                "clientes": [
                    {
                        "partner_id": p.id,
                        "nombre": p.name,
                        "direccion": ", ".join(filter(None, [p.street, p.city])),
                        "saldo_facturas": p.credit,
                        "consumos": p.total_invoiced,
                        "fecha_ultima_entrega": (
                            p.fecha_ultima_entrega.isoformat()
                            if p.fecha_ultima_entrega
                            else None
                        ),
                        "fecha_ultima_cobranza": (
                            p.fecha_ultima_cobranza.isoformat()
                            if p.fecha_ultima_cobranza
                            else None
                        ),
                        "fue_vencido": bool(getattr(p, "fue_vencido", False)),
                        "fuc_vencido": bool(getattr(p, "fuc_vencido", False)),
                        "al_dia": bool(not getattr(p, "fue_vencido", False) and not getattr(p, "fuc_vencido", False)),
                    }
                    for p in partners
                ]
            }
        )

    @http.route(
        "/insa/api/ciudades",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def ciudades(self, **kw):
        """Alta rápida desde la app (Paso 2.7): lista de insa.city para
        poblar el selector, mismos modelos que el formulario público de
        alta (feature-formulario-alta)."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        ciudades = request.env["insa.city"].sudo().search([])
        return self._json_response(
            {"ciudades": [{"city_id": c.id, "nombre": c.name} for c in ciudades]}
        )

    @http.route(
        "/insa/api/calles",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def calles(self, **kw):
        """Paso 2.7: calles de una ciudad puntual — filtrado en el
        servidor (`?city_id=X`), no se baja la tabla completa al celular."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        city_id = kw.get("city_id")
        if not city_id:
            return self._json_response({"error": "Falta 'city_id'."}, status=400)

        calles = (
            request.env["insa.street"]
            .sudo()
            .search([("city_id", "=", int(city_id))])
        )
        return self._json_response(
            {"calles": [{"street_id": c.id, "nombre": c.name} for c in calles]}
        )

    @http.route(
        "/insa/api/clientes",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def crear_cliente(self, **kw):
        """Alta rápida desde la app (Paso 2.7). A diferencia del
        formulario público (feature-formulario-alta, que deja el contacto
        como lead sin tocar customer_rank), acá SÍ se marca
        customer_rank=1 al crear: la app solo la usan repartidores
        autenticados con la credencial de su ruta — a diferencia del
        formulario web, abierto a cualquiera — así que el cliente queda
        utilizable de inmediato (Buscador, registrar una visita), en vez
        de esperar revisión de INSA. Dispara la creación de su ubicación
        de stock (ver res_partner.py, create()).
        """
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        nombre = (body.get("nombre") or "").strip()
        city_id = body.get("city_id")
        street_id = body.get("street_id")
        street_number = (body.get("street_number") or "").strip()
        if not nombre or not city_id or not street_id or not street_number:
            return self._json_response(
                {"error": "Faltan 'nombre', 'city_id', 'street_id' o 'street_number'."},
                status=400,
            )

        city = request.env["insa.city"].sudo().browse(int(city_id))
        street = request.env["insa.street"].sudo().browse(int(street_id))
        if not city.exists() or not street.exists():
            return self._json_response(
                {"error": "Ciudad o calle inexistente."}, status=400
            )

        mobile = (body.get("mobile") or "").strip()
        street_display = f"{street.name} {street_number}".strip()

        # C-20: etiqueta "Alta rápida" para que administración identifique
        # rápido qué contactos entraron así (nombre, dirección y teléfono
        # nomás, sin datos de facturación) — se busca-o-crea por nombre en
        # vez de un data XML fijo, para no acoplar el módulo a un xml_id
        # que otra instalación podría no tener.
        etiqueta_alta_rapida = request.env["res.partner.category"].sudo().search(
            [("name", "=", "Alta rápida")], limit=1
        )
        if not etiqueta_alta_rapida:
            etiqueta_alta_rapida = request.env["res.partner.category"].sudo().create(
                {"name": "Alta rápida"}
            )

        company_type = body.get("company_type") or "person"
        is_company = (company_type == "company")
        vat = (body.get("vat") or "").strip()
        frecuencia_entrega = body.get("frecuencia_entrega") or "semanal"
        dias_entrega = (body.get("dias_entrega") or "").strip()

        partner = request.env["res.partner"].sudo().create(
            {
                "name": nombre,
                "company_type": company_type,
                "is_company": is_company,
                "vat": vat or False,
                "mobile": mobile or False,
                "street": street_display,
                "city": city.name,
                "customer_rank": 1,
                # type="fsm_location": sin esto el cliente no tiene
                # fsm.location y no se le puede generar una parada en
                # ninguna hoja de ruta (C-18, action_generar_hoja_ruta).
                "type": "fsm_location",
                "category_id": [(4, etiqueta_alta_rapida.id)],
                "estado_completitud": "incompleto",
                "insa_city_id": city.id,
                "insa_street_id": street.id,
                "insa_street_number": street_number,
                "insa_floor": body.get("floor") or False,
                "insa_apartment": body.get("apartment") or False,
                "insa_tower": body.get("tower") or False,
                "insa_block": body.get("block") or False,
                "insa_lot": body.get("lot") or False,
                "insa_address_notes": body.get("address_notes") or False,
                "insa_tax_condition": body.get("tax_condition") or "consumidor_final",
                "insa_order_notes": body.get("order_notes") or False,
                "insa_frecuencia_entrega": frecuencia_entrega,
                "insa_dias_entrega": dias_entrega or False,
            }
        )

        # Asegurar fsm.location para que fieldservice (OCA) y fsm.order puedan referenciarlo
        Location = request.env["fsm.location"].sudo()
        location = Location.search([("partner_id", "=", partner.id)], limit=1)
        if not location:
            location = Location.create(
                {
                    "partner_id": partner.id,
                    "name": partner.name,
                    "fsm_route_id": cred.route_id.id,
                }
            )

        # C-20: entra a la plantilla del reparto del token para el día indicado / hoy
        date_str = body.get("date")
        if date_str:
            try:
                target_date = date.fromisoformat(date_str)
            except ValueError:
                target_date = date.today()
        else:
            target_date = date.today()

        dia_semana_target = str(target_date.weekday())
        Plantilla = request.env["insa.ruta.plantilla"].sudo()
        plantilla = Plantilla.search(
            [("route_id", "=", cred.route_id.id), ("dia_semana", "=", dia_semana_target)],
            limit=1,
        )
        if not plantilla:
            plantilla = Plantilla.create(
                {"route_id": cred.route_id.id, "dia_semana": dia_semana_target}
            )
        plantilla.action_agregar_cliente(partner.id)

        return self._json_response(
            {
                "partner_id": partner.id,
                "nombre": partner.name,
                "direccion": ", ".join(filter(None, [partner.street, partner.city])),
                "saldo_facturas": partner.credit,
                "consumos": partner.total_invoiced,
            }
        )

    @http.route(
        "/insa/api/clientes/agregar-a-ruta",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def agregar_cliente_a_ruta(self, **kw):
        """Agrega un cliente a la hoja de ruta del día como Pedido Extra (insa.pedido.extra, RN-17)."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        partner_id = body.get("partner_id")
        if not partner_id:
            return self._json_response({"error": "Falta 'partner_id'."}, status=400)

        date_str = body.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        if fecha != date.today():
            return self._json_response(
                {
                    "error": "Solo se pueden agregar clientes en la hoja de ruta del día actual (modo solo lectura para otras fechas)."
                },
                status=403,
            )

        partner = request.env["res.partner"].sudo().browse(int(partner_id))
        if not partner.exists():
            return self._json_response({"error": "Cliente inexistente."}, status=404)

        Dayroute = request.env["fsm.route.dayroute"].sudo()
        hoja = Dayroute.search(
            [("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1
        )
        if not hoja:
            hoja = Dayroute.create({"route_id": cred.route_id.id, "date": fecha})

        PedidoExtra = request.env["insa.pedido.extra"].sudo()
        pedido_existente = PedidoExtra.search(
            [("hoja_ruta_id", "=", hoja.id), ("partner_id", "=", partner.id)], limit=1
        )
        if not pedido_existente:
            pedido_existente = PedidoExtra.create(
                {
                    "hoja_ruta_id": hoja.id,
                    "partner_id": partner.id,
                    "nota": "Agregado fuera de ruta desde la app móvil",
                }
            )

        return self._json_response(
            {
                "success": True,
                "pedido_extra_id": pedido_existente.id,
                "partner_id": partner.id,
                "message": "Cliente agregado a Pedidos extra correctamente.",
            }
        )

    @http.route(
        "/insa/api/clientes/quitar-de-ruta",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def quitar_cliente_de_ruta(self, **kw):
        """Quita un cliente de la hoja de ruta del día, detectando y eliminando de ruta original (fsm.order) o pedidos extra (insa.pedido.extra)."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        partner_id = body.get("partner_id")
        if not partner_id:
            return self._json_response({"error": "Falta 'partner_id'."}, status=400)

        date_str = body.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        if fecha != date.today():
            return self._json_response(
                {
                    "error": "Solo se pueden quitar clientes en la hoja de ruta del día actual (modo solo lectura para otras fechas)."
                },
                status=403,
            )

        Dayroute = request.env["fsm.route.dayroute"].sudo()
        hoja = Dayroute.search(
            [("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1
        )
        eliminado_de = []
        if hoja:
            # 1. Validar y eliminar si era un pedido extra
            PedidoExtra = request.env["insa.pedido.extra"].sudo()
            pedidos = PedidoExtra.search(
                [
                    ("hoja_ruta_id", "=", hoja.id),
                    ("partner_id", "=", int(partner_id)),
                ]
            )
            if pedidos:
                pedidos.unlink()
                eliminado_de.append("pedidos_extra")

            # 2. Validar y eliminar si estaba en la ruta original (fsm.order)
            Order = request.env["fsm.order"].sudo()
            orders = Order.search(
                [
                    ("dayroute_id", "=", hoja.id),
                    ("location_id.partner_id", "=", int(partner_id)),
                ]
            )
            if orders:
                orders.unlink()
                eliminado_de.append("ruta_original")

        return self._json_response(
            {
                "success": True,
                "partner_id": int(partner_id),
                "eliminado_de": eliminado_de,
                "message": (
                    f"Cliente quitado correctamente de: {', '.join(eliminado_de)}"
                    if eliminado_de
                    else "Cliente no encontrado en la hoja de ruta."
                ),
            }
        )

    @http.route(
        "/insa/api/hoja-de-ruta",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def hoja_de_ruta(self, **kw):
        """Primer endpoint de datos D3 más allá de login/whoami: la hoja de
        ruta del día de la ruta del token (nunca de un route_id que mande
        el cliente) con su lista de clientes a visitar.

        `?date=YYYY-MM-DD` (default: hoy). Sin hoja de ruta para esa
        fecha/ruta: 200 con `hoja_ruta: null`, no 404 — es un estado
        esperado (todavía no se armó/asignó), no un error.

        Clientes = fsm.order (OCA, "parada agendada hoy") de
        `hoja.order_ids`, cruzados con insa.movimiento.cliente (la visita
        ya registrada, si la hay) para el estado visitado/resultado/cobro,
        más insa.pedido.extra de `hoja.pedido_extra_ids` (supuesto 0.3),
        marcados con `es_extra: true` para que la app los distinga
        visualmente de las paradas regulares del día.
        saldo_facturas/consumos salen de res.partner (credit/total_invoiced,
        reales de account/sale). fecha_ultima_entrega/fecha_ultima_cobranza
        (Paso 1.12) son ahora campos dedicados y persistidos en
        res.partner (antes se calculaban on-the-fly en cada request).
        """
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        date_str = kw.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        es_hoy = (fecha == date.today())
        Dayroute = request.env["fsm.route.dayroute"].sudo()
        hoja = Dayroute.search(
            [("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1
        )
        if not hoja:
            if es_hoy:
                hoja = Dayroute.create({"route_id": cred.route_id.id, "date": fecha})
            else:
                return self._json_response({"hoja_ruta": None, "clientes": [], "solo_lectura": True})

        clientes = [self._serializar_cliente(hoja, order) for order in hoja.order_ids]
        clientes += [
            self._serializar_pedido_extra(pedido) for pedido in hoja.pedido_extra_ids
        ]
        # Paso 2.6 (Buscador modo 2): ventas puntuales a clientes fuera de
        # la hoja de ruta de hoy — sin esto, quedarían guardadas en Odoo
        # pero invisibles en la app después de cargarlas.
        partners_en_ruta = hoja.order_ids.mapped("location_id.partner_id")
        partners_pedidos_extra = hoja.pedido_extra_ids.mapped("partner_id")
        movimientos_fuera_de_ruta = hoja.movimiento_cliente_ids.filtered(
            lambda m: m.partner_id not in partners_en_ruta and m.partner_id not in partners_pedidos_extra
        )
        clientes += [
            self._serializar_movimiento_fuera_de_ruta(mov) for mov in movimientos_fuera_de_ruta
        ]
        return self._json_response(
            {
                "hoja_ruta": {
                    "id": hoja.id,
                    "date": str(hoja.date),
                    "state": hoja.state,
                    "reparto_numero": hoja.reparto_numero,
                },
                "clientes": clientes,
                "solo_lectura": not es_hoy,
                # Paso 1.12 (FUC): umbral configurable, no hardcodeado en
                # la app — reemplaza el corte fijo saldoFacturas > 30000
                # que hoy vive en el cliente TS (Paso 2.10). Default 30000
                # para no cambiar el comportamiento actual hasta que se
                # reconfigure a propósito.
                "fuc_umbral_saldo": float(
                    request.env["ir.config_parameter"]
                    .sudo()
                    .get_param("insa_reparto.fuc_umbral_saldo", "30000")
                ),
            }
        )

    @http.route(
        "/insa/api/visita",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def registrar_visita(self, **kw):
        """Crea o actualiza el insa.movimiento.cliente de un cliente en la
        hoja de ruta del día. Upsert (no siempre-crear): GET /hoja-de-ruta
        ya asume "un movimiento por cliente por hoja"
        (`movimiento_cliente_ids.filtered(partner)[:1]`), así que un
        segundo POST para el mismo cliente/hoja actualiza el existente en
        vez de duplicarlo.

        No confirma movimientos de stock de envase (action_confirmar):
        eso sigue siendo un paso aparte, igual que en el form web.
        """
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        partner_id = body.get("partner_id")
        if not partner_id:
            return self._json_response({"error": "Falta 'partner_id'."}, status=400)

        date_str = body.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        if fecha != date.today():
            return self._json_response(
                {
                    "error": "Solo se pueden registrar visitas en la hoja de ruta del día actual (modo solo lectura para otras fechas)."
                },
                status=403,
            )

        Dayroute = request.env["fsm.route.dayroute"].sudo()
        hoja = Dayroute.search(
            [("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1
        )
        if not hoja:
            return self._json_response(
                {"error": "No hay hoja de ruta para esta ruta en esa fecha."},
                status=404,
            )
        if hoja.state != "en_reparto":
            return self._json_response(
                {
                    "error": "La hoja de ruta no está 'En reparto', no se "
                    "puede registrar la visita."
                },
                status=400,
            )

        partner = request.env["res.partner"].sudo().browse(int(partner_id))
        if not partner.exists():
            return self._json_response({"error": "Cliente inexistente."}, status=400)

        order = hoja.order_ids.filtered(
            lambda o, p=partner: o.location_id.partner_id == p
        )[:1]
        pedido_extra = hoja.pedido_extra_ids.filtered(
            lambda pe, p=partner: pe.partner_id == p
        )[:1]
        # Paso 2.6 (Buscador modo 2): un cliente fuera de la hoja de ruta
        # de hoy también puede recibir una visita/venta puntual — ya no se
        # rechaza de plano, solo se exige que sea un cliente real
        # (customer_rank > 0, mismo criterio que /insa/api/clientes/buscar
        # — no type == "fsm_location", que es el tipo que OCA fieldservice
        # usa para el partner de la ubicación, no del cliente).
        if not order and not pedido_extra:
            if partner.customer_rank <= 0:
                try:
                    partner.sudo().write({"customer_rank": 1})
                except Exception:
                    pass

        mov = hoja.movimiento_cliente_ids.filtered(
            lambda m, p=partner: m.partner_id == p
        )[:1]
        cobro_monto = float(body.get("cobro_efectivo") or body.get("cobro_monto") or 0.0)
        medio_pago = body.get("medio_pago") or "efectivo"
        referencia_pago = (body.get("referencia_pago") or "").strip()

        resultado = body.get("resultado") or False
        tiene_lineas_articulo = bool(body.get("lineas_articulo"))
        tiene_lineas_envase = bool(body.get("lineas_envase"))

        # Determinar si la visita es sin transacción
        es_sin_transaccion = (
            medio_pago == "sin_transaccion"
            or bool(resultado)
            or (cobro_monto <= 0 and not tiene_lineas_articulo and not tiene_lineas_envase)
        )

        if es_sin_transaccion:
            cobro_monto = 0.0
            medio_pago_val = False
        else:
            medio_pago_val = medio_pago if medio_pago in ("efectivo", "cheque", "transferencia") else "efectivo"

        vals = {
            "hoja_ruta_id": hoja.id,
            "partner_id": partner.id,
            "resultado": resultado,
            "cobro_efectivo": cobro_monto,
            "medio_pago": medio_pago_val,
            # C-03: sin 'order' es un cliente fuera de la ruta fija del
            # día — el repartidor lo encontró con el buscador (Paso 2.6).
            # 'crear_cliente' (alta rápida, C-20) y el buscador son las
            # dos puertas de la app hacia acá; las dos son "repartidor".
            "es_extra": bool(not order),
            "origen_extra": "repartidor" if not order else False,
        }

        # Autoincrementar secuencia si es un nuevo movimiento (1, 2, 3...)
        if not mov or mov.secuencia == 0:
            secuencias = hoja.movimiento_cliente_ids.mapped("secuencia")
            max_seq = max(secuencias) if secuencias else 0
            vals["secuencia"] = max_seq + 1

        # Si no tiene order ni pedido extra previo, crear insa.pedido.extra atendido
        if not order and not pedido_extra:
            try:
                pedido_extra = request.env["insa.pedido.extra"].sudo().create({
                    "hoja_ruta_id": hoja.id,
                    "partner_id": partner.id,
                    "atendido": True,
                    "nota": "Agregado por repartidor fuera de ruta",
                })
                vals["pedido_extra_id"] = pedido_extra.id
            except Exception:
                pass
            vals["es_extra"] = True
        elif pedido_extra:
            vals["pedido_extra_id"] = pedido_extra.id
            vals["es_extra"] = True
            try:
                pedido_extra.write({"atendido": True})
            except Exception:
                pass

        if "visitado" in body:
            vals["visitado"] = bool(body.get("visitado"))

        try:
            if "lineas_articulo" in body and body.get("lineas_articulo"):
                salida = hoja.control_playa_ids.filtered(
                    lambda c: c.tipo == "salida" and c.state == "confirmado"
                )[:1]
                if not salida:
                    return self._json_response(
                        {
                            "error": "El Control de Playa de Salida debe estar aprobado para registrar artículos."
                        },
                        status=400,
                    )

                # Validar stock cargado en salida vs vendido
                otros_movs = hoja.movimiento_cliente_ids.filtered(
                    lambda m, current_p=partner.id: m.partner_id.id != current_p
                )
                otras_lineas_articulo = otros_movs.mapped("linea_articulo_ids")

                for linea in body.get("lineas_articulo") or []:
                    p_id = linea.get("product_id")
                    tipo = linea.get("tipo") or "venta"
                    cant = float(linea.get("cantidad", 1.0))

                    if tipo == "venta" and cant > 0:
                        salida_linea = salida.linea_ids.filtered(
                            lambda l, p=p_id: l.product_id.id == p
                        )[:1]
                        if not salida_linea:
                            prod_name = (
                                request.env["product.template"].sudo().browse(p_id).name
                            )
                            return self._json_response(
                                {
                                    "error": f"El artículo '{prod_name}' no fue cargado en el Control de Playa de Salida."
                                },
                                status=400,
                            )

                        stock_salida = salida_linea.cantidad
                        ya_vendido_otros = sum(
                            otras_lineas_articulo.filtered(
                                lambda l, p=p_id: l.product_id.id == p and l.tipo == "venta"
                            ).mapped("cantidad")
                        )
                        stock_disponible = stock_salida - ya_vendido_otros
                        if cant > stock_disponible:
                            prod_name = salida_linea.product_id.name
                            return self._json_response(
                                {
                                    "error": f"Stock insuficiente para '{prod_name}'. "
                                    f"Stock disponible en camión: {int(stock_disponible) if stock_disponible.is_integer() else stock_disponible}, "
                                    f"intentando asignar: {int(cant) if cant.is_integer() else cant}."
                                },
                                status=400,
                            )

            if mov:
                mov.write(vals)
            else:
                mov = request.env["insa.movimiento.cliente"].sudo().create(vals)

            if "lineas_articulo" in body:
                mov.linea_articulo_ids.unlink()
                for linea in body.get("lineas_articulo") or []:
                    request.env["insa.movimiento.cliente.linea.articulo"].sudo().create(
                        {
                            "movimiento_id": mov.id,
                            "product_id": linea.get("product_id"),
                            "tipo": linea.get("tipo") or "venta",
                            "cantidad": linea.get("cantidad", 1.0),
                            "precio_unitario": linea.get("precio_unitario", 0.0),
                            # C-06: la devolución de producto no conforme
                            # se carga en el momento, en la visita del
                            # cliente, con su motivo. cambio_directo por
                            # default True: el caso normal es que se le
                            # deja uno nuevo en el acto.
                            "motivo": linea.get("motivo") or False,
                            "cambio_directo": linea.get("cambio_directo", True),
                        }
                    )
            if "lineas_envase" in body:
                mov.linea_envase_ids.unlink()
                for linea in body.get("lineas_envase") or []:
                    request.env["insa.movimiento.cliente.linea.envase"].sudo().create(
                        {
                            "movimiento_id": mov.id,
                            "product_id": linea.get("product_id"),
                            "tipo": linea.get("tipo"),
                            "cantidad": linea.get("cantidad", 1.0),
                        }
                    )

            # Integración de cobros con account.payment y account.move
            if cobro_monto > 0:
                try:
                    Journal = request.env["account.journal"].sudo()
                    company = partner.company_id or request.env.company
                    journal_type = "cash" if medio_pago == "efectivo" else "bank"
                    journal = Journal.search(
                        [("type", "=", journal_type), ("company_id", "=", company.id)],
                        limit=1,
                    )
                    if not journal:
                        journal = Journal.search([("type", "=", journal_type)], limit=1)

                    if journal:
                        payment_method_line = journal.inbound_payment_method_line_ids[:1]
                        memo = f"Reparto {cred.route_id.numero_reparto} - {hoja.name or fecha.isoformat()} - {partner.name}"
                        if referencia_pago:
                            memo += f" - Ref: {referencia_pago}"

                        payment_vals = {
                            "payment_type": "inbound",
                            "partner_type": "customer",
                            "partner_id": partner.id,
                            "amount": cobro_monto,
                            "date": fecha,
                            "journal_id": journal.id,
                            "memo": memo,
                        }
                        if payment_method_line:
                            payment_vals["payment_method_line_id"] = payment_method_line.id

                        payment = request.env["account.payment"].sudo().create(payment_vals)
                        try:
                            payment.action_post()
                        except Exception:
                            pass

                        # Si el cobro es por banco/transferencia, asegurar que los datos
                        # queden asentados en account.move
                        if medio_pago != "efectivo" and payment.move_id and referencia_pago:
                            payment.move_id.write({"ref": referencia_pago})
                except Exception:
                    pass

            if order:
                return self._json_response(self._serializar_cliente(hoja, order))
            if pedido_extra:
                return self._json_response(self._serializar_pedido_extra(pedido_extra))
            return self._json_response(self._serializar_movimiento_fuera_de_ruta(mov))
        except (UserError, ValidationError) as e:
            return self._json_response({"error": str(e)}, status=400)
        except Exception as e:
            return self._json_response({"error": f"Error al registrar visita: {str(e)}"}, status=500)

    def _serializar_cliente(self, hoja, order):
        partner = order.location_id.partner_id
        mov = hoja.movimiento_cliente_ids.filtered(
            lambda m, p=partner: m.partner_id == p
        )[:1]
        pedido_extra = hoja.pedido_extra_ids.filtered(
            lambda pe, p=partner: pe.partner_id == p
        )[:1]
        monto_visita = (
            sum(mov.linea_articulo_ids.filtered(lambda l: l.tipo == "venta").mapped("subtotal"))
            if mov
            else 0.0
        )
        return {
            "partner_id": partner.id,
            "nombre": partner.name,
            "direccion": ", ".join(filter(None, [partner.street, partner.city])),
            "saldo_facturas": partner.credit,
            "consumos": (partner.total_invoiced or 0.0) + monto_visita,
            "fecha_ultima_entrega": (
                partner.fecha_ultima_entrega.isoformat()
                if partner.fecha_ultima_entrega
                else None
            ),
            "fecha_ultima_cobranza": (
                partner.fecha_ultima_cobranza.isoformat()
                if partner.fecha_ultima_cobranza
                else None
            ),
            "fue_vencido": bool(getattr(partner, "fue_vencido", False)),
            "fuc_vencido": bool(getattr(partner, "fuc_vencido", False)),
            "al_dia": bool(not getattr(partner, "fue_vencido", False) and not getattr(partner, "fuc_vencido", False)),
            # Supuesto 0.2 / RN textual de Nicolás: "pendiente" es "ningún
            # movimiento cargado", cualquiera sea su tipo. No usar
            # mov.visitado acá: ese campo es un concepto distinto (RN-18,
            # "hubo visita física") que puede ser False a propósito en un
            # sin_gestion resuelto por WhatsApp sin ir al domicilio, y ese
            # caso también debe salir de "Pendientes" en la app.
            "visitado": bool(mov),
            "resultado": mov.resultado or None if mov else None,
            "cobro_efectivo": mov.cobro_efectivo if mov else 0.0,
            "medio_pago": (
                mov.medio_pago
                if (mov and mov.medio_pago)
                else ("sin_transaccion" if (mov and not mov.cobro_efectivo and not mov.tiene_venta) else "efectivo")
            ),
            "secuencia": mov.secuencia if mov else 0,
            "lineas_articulo": [
                {
                    "product_id": linea.product_id.id,
                    "nombre": linea.product_id.name,
                    "tipo": linea.tipo,
                    "cantidad": linea.cantidad,
                    "precio_unitario": linea.precio_unitario,
                }
                for linea in mov.linea_articulo_ids
            ] if mov else [],
            "lineas_envase": [
                {
                    "product_id": linea.product_id.id,
                    "nombre": linea.product_id.name,
                    "tipo": linea.tipo,
                    "cantidad": linea.cantidad,
                }
                for linea in mov.linea_envase_ids
            ] if mov else [],
            "iconos_visita": (mov.iconos_visita or "") if mov else "",
            "es_extra": bool(pedido_extra),
            "pedido_extra_id": pedido_extra.id if pedido_extra else None,
            "fuera_de_ruta": False,
            "deuda_envases": self._deuda_envases(partner),
            "saldos_envase": self._saldos_envase(partner),
            "pedido_habitual": self._pedido_habitual(partner),
            "nota": pedido_extra.nota or "" if pedido_extra else "",
            "articulos_solicitados": [
                {
                    "product_id": linea.product_id.id,
                    "producto": linea.product_id.name,
                    "cantidad": linea.cantidad,
                }
                for linea in pedido_extra.linea_ids
            ] if pedido_extra else [],
        }

    def _serializar_movimiento_fuera_de_ruta(self, mov):
        # Paso 2.6 (Buscador modo 2): un insa.movimiento.cliente cuyo
        # partner no está en hoja.order_ids — venta puntual a un cliente
        # fuera de la hoja de ruta de hoy. Desde C-03 esto ES la definición
        # de es_extra ("no proviene de la ruta fija del día"): antes de
        # esa reunión el campo solo existía en insa.pedido.extra (el
        # encargo cargado por administración, RN-17) y acá se devolvía
        # es_extra=False a propósito porque no había pedido previo — con
        # el listado unificado, ambos casos son "extra" y se marcan igual.
        partner = mov.partner_id
        monto_visita = sum(
            mov.linea_articulo_ids.filtered(lambda l: l.tipo == "venta").mapped("subtotal")
        )
        return {
            "partner_id": partner.id,
            "nombre": partner.name,
            "direccion": ", ".join(filter(None, [partner.street, partner.city])),
            "saldo_facturas": partner.credit,
            "consumos": (partner.total_invoiced or 0.0) + monto_visita,
            "fecha_ultima_entrega": (
                partner.fecha_ultima_entrega.isoformat()
                if partner.fecha_ultima_entrega
                else None
            ),
            "fecha_ultima_cobranza": (
                partner.fecha_ultima_cobranza.isoformat()
                if partner.fecha_ultima_cobranza
                else None
            ),
            "fue_vencido": bool(getattr(partner, "fue_vencido", False)),
            "fuc_vencido": bool(getattr(partner, "fuc_vencido", False)),
            "al_dia": bool(not getattr(partner, "fue_vencido", False) and not getattr(partner, "fuc_vencido", False)),
            "visitado": True,
            "resultado": mov.resultado or None,
            "cobro_efectivo": mov.cobro_efectivo,
            "medio_pago": (
                mov.medio_pago
                if mov.medio_pago
                else ("sin_transaccion" if (not mov.cobro_efectivo and not mov.tiene_venta) else "efectivo")
            ),
            "secuencia": mov.secuencia,
            "referencia_pago": getattr(mov, "referencia_pago", None) if mov else None,
            "lineas_articulo": [
                {
                    "product_id": linea.product_id.id,
                    "nombre": linea.product_id.name,
                    "tipo": linea.tipo,
                    "cantidad": linea.cantidad,
                    "precio_unitario": linea.precio_unitario,
                }
                for linea in mov.linea_articulo_ids
            ],
            "lineas_envase": [
                {
                    "product_id": linea.product_id.id,
                    "nombre": linea.product_id.name,
                    "tipo": linea.tipo,
                    "cantidad": linea.cantidad,
                }
                for linea in mov.linea_envase_ids
            ],
            "iconos_visita": mov.iconos_visita or "",
            "es_extra": mov.es_extra,
            "pedido_extra_id": mov.pedido_extra_id.id or None,
            "fuera_de_ruta": True,
            "deuda_envases": self._deuda_envases(partner),
            "saldos_envase": self._saldos_envase(partner),
            "pedido_habitual": self._pedido_habitual(partner),
            "articulos_solicitados": [
                {
                    "product_id": linea.product_id.id,
                    "producto": linea.product_id.name,
                    "cantidad": linea.cantidad,
                }
                for linea in mov.pedido_extra_id.linea_ids
            ] if mov.pedido_extra_id else [],
        }

    def _deuda_envases(self, partner):
        # Paso 1.10 / RN 3.4 del doc D1: "la app le muestra al repartidor,
        # antes de la visita, los clientes con deuda de envases para que
        # los reclame". Solo los que tienen algo por recuperar.
        saldos = (
            request.env["insa.saldo.envase"]
            .sudo()
            .search(
                [
                    ("partner_id", "=", partner.id),
                    ("cantidad_a_recuperar", ">", 0),
                ]
            )
        )
        return [
            {
                "product_id": saldo.product_id.id,
                "producto": saldo.product_id.name,
                "saldo": saldo.saldo,
                "permitido": saldo.permitido,
                "cantidad_a_recuperar": saldo.cantidad_a_recuperar,
            }
            for saldo in saldos
        ]

    def _saldos_envase(self, partner):
        return [
            {
                "product_id": saldo.product_id.id,
                "producto": saldo.product_id.name,
                "saldo": saldo.saldo,
                "permitido": saldo.permitido,
                "cantidad_a_recuperar": saldo.cantidad_a_recuperar,
                "estado_saldo": getattr(saldo, "estado_saldo", "al_dia"),
            }
            for saldo in getattr(partner, "insa_saldo_envase_ids", [])
        ]

    def _pedido_habitual(self, partner):
        # Revisión 07/10: incluye lo que viene de un abono activo (ver
        # res.partner._pedido_habitual_efectivo) — la app ve la misma
        # cantidad por visita que precarga el control de salida.
        return [
            {"product_id": producto.id, "producto": producto.name, "cantidad": cantidad}
            for producto, cantidad in partner._pedido_habitual_efectivo().items()
        ]

    def _serializar_pedido_extra(self, pedido):
        partner = pedido.partner_id
        hoja = pedido.hoja_ruta_id
        mov = hoja.movimiento_cliente_ids.filtered(
            lambda m, p=partner: m.partner_id == p
        )[:1]
        monto_visita = (
            sum(mov.linea_articulo_ids.filtered(lambda l: l.tipo == "venta").mapped("subtotal"))
            if mov
            else 0.0
        )
        return {
            "partner_id": partner.id,
            "nombre": partner.name,
            "direccion": ", ".join(filter(None, [partner.street, partner.city])),
            "saldo_facturas": partner.credit,
            "consumos": (partner.total_invoiced or 0.0) + monto_visita,
            "fecha_ultima_entrega": (
                partner.fecha_ultima_entrega.isoformat()
                if partner.fecha_ultima_entrega
                else None
            ),
            "fecha_ultima_cobranza": (
                partner.fecha_ultima_cobranza.isoformat()
                if partner.fecha_ultima_cobranza
                else None
            ),
            "fue_vencido": bool(getattr(partner, "fue_vencido", False)),
            "fuc_vencido": bool(getattr(partner, "fuc_vencido", False)),
            "al_dia": bool(not getattr(partner, "fue_vencido", False) and not getattr(partner, "fuc_vencido", False)),
            "visitado": bool(mov.visitado if mov else pedido.atendido),
            "resultado": mov.resultado if mov else None,
            "cobro_efectivo": mov.cobro_efectivo if mov else 0.0,
            "medio_pago": (
                mov.medio_pago
                if (mov and mov.medio_pago)
                else ("sin_transaccion" if (mov and not mov.cobro_efectivo and not mov.tiene_venta) else "efectivo")
            ),
            "secuencia": mov.secuencia if mov else 0,
            "referencia_pago": getattr(mov, "referencia_pago", None) if mov else None,
            "lineas_articulo": [
                {
                    "product_id": linea.product_id.id,
                    "nombre": linea.product_id.name,
                    "tipo": linea.tipo,
                    "cantidad": linea.cantidad,
                    "precio_unitario": linea.precio_unitario,
                }
                for linea in mov.linea_articulo_ids
            ] if mov else [],
            "lineas_envase": [
                {
                    "product_id": linea.product_id.id,
                    "nombre": linea.product_id.name,
                    "tipo": linea.tipo,
                    "cantidad": linea.cantidad,
                }
                for linea in mov.linea_envase_ids
            ] if mov else [],
            "iconos_visita": (mov.iconos_visita or "") if mov else "",
            "es_extra": True,
            "pedido_extra_id": pedido.id,
            "fuera_de_ruta": True,
            "deuda_envases": self._deuda_envases(partner),
            "saldos_envase": self._saldos_envase(partner),
            "pedido_habitual": self._pedido_habitual(partner),
            "nota": pedido.nota or "",
            "articulos_solicitados": [
                {
                    "product_id": linea.product_id.id,
                    "producto": linea.product_id.name,
                    "cantidad": linea.cantidad,
                }
                for linea in pedido.linea_ids
            ],
        }

    @http.route(
        "/insa/api/control-playa",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def control_playa(self, **kw):
        """Paso 1.6: lee el control de playa de salida y entrada de la
        hoja de ruta del día de la ruta del token."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        date_str = kw.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        hoja = (
            request.env["fsm.route.dayroute"]
            .sudo()
            .search([("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1)
        )
        if not hoja:
            return self._json_response({"salida": None, "entrada": None})

        salida = hoja.control_playa_ids.filtered(lambda c: c.tipo == "salida")[:1]
        if not salida and hoja.state in ("borrador", "en_reparto"):
            salida = (
                request.env["insa.control.playa"]
                .sudo()
                .create({"hoja_ruta_id": hoja.id, "tipo": "salida"})
            )
        elif salida and not salida.linea_ids and hoja.state in ("borrador", "en_reparto"):
            salida._precargar_pedido_habitual()
        entrada = hoja.control_playa_ids.filtered(lambda c: c.tipo == "entrada")[:1]
        return self._json_response(
            {
                "salida": self._serializar_control_playa(salida) if salida else None,
                "entrada": self._serializar_control_playa(entrada) if entrada else None,
            }
        )

    @http.route(
        "/insa/api/gasto",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def registrar_gasto(self, **kw):
        """Revisión 06/10: la pantalla "Elegir tipo" de gastos (Combustible,
        Peaje, Comida, Estacionamiento, Mantenimiento, Otro) ya existía en
        la app sin backend — este es el endpoint que le faltaba. Sin
        aprobación: queda registrado apenas el repartidor lo carga, y se
        descuenta del efectivo esperado en Liquidación
        (fsm.route.dayroute._compute_efectivo)."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        tipo = body.get("tipo")
        tipos_validos = (
            "combustible",
            "peaje",
            "comida",
            "estacionamiento",
            "mantenimiento",
            "otro",
        )
        if tipo not in tipos_validos:
            return self._json_response(
                {"error": "'tipo' debe ser uno de: %s." % ", ".join(tipos_validos)},
                status=400,
            )
        importe = body.get("importe")
        if not importe or importe <= 0:
            return self._json_response(
                {"error": "'importe' tiene que ser mayor a 0."}, status=400
            )

        date_str = body.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        hoja = (
            request.env["fsm.route.dayroute"]
            .sudo()
            .search([("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1)
        )
        if not hoja:
            return self._json_response(
                {"error": "No hay hoja de ruta para esta ruta en esa fecha."},
                status=404,
            )

        gasto = (
            request.env["insa.gasto.reparto"]
            .sudo()
            .create(
                {
                    "hoja_ruta_id": hoja.id,
                    "tipo": tipo,
                    "importe": importe,
                    # La app manda la foto en base64 (sin el prefijo
                    # data:image/...;base64,); fields.Image la procesa
                    # igual que cualquier Binary.
                    "comprobante": body.get("comprobante") or False,
                    "observacion": body.get("observacion") or "",
                }
            )
        )
        return self._json_response(
            {
                "id": gasto.id,
                "tipo": gasto.tipo,
                "importe": gasto.importe,
                "fecha_hora": gasto.fecha_hora.isoformat(),
            }
        )

    @http.route(
        "/insa/api/stock-en-ruta",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def stock_en_ruta(self, **kw):
        """Devuelve el balance en vivo de stock en el camión para la fecha.
        Se calcula a partir del Control de Playa de Salida aprobado y se descuentan
        las ventas/cambios y se suman los envases recuperados en las visitas registradas."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        date_str = kw.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        hoja = (
            request.env["fsm.route.dayroute"]
            .sudo()
            .search([("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1)
        )
        if not hoja:
            return self._json_response({"productos": []})

        salida = hoja.control_playa_ids.filtered(
            lambda c: c.tipo == "salida" and c.state == "confirmado"
        )[:1]
        if not salida:
            return self._json_response({"productos": []})

        movimientos = hoja.movimiento_cliente_ids
        lineas_articulo = movimientos.mapped("linea_articulo_ids")
        lineas_envase = movimientos.mapped("linea_envase_ids")

        productos_stock = []
        for linea in salida.linea_ids:
            prod_id = linea.product_id.id
            salida_llenos = linea.cantidad
            salida_vacios = linea.cantidad_vacia
            salida_fallados = linea.cantidad_fallada

            entregados = sum(
                lineas_articulo.filtered(
                    lambda l, p=prod_id: l.product_id.id == p and l.tipo == "venta"
                ).mapped("cantidad")
            )
            articulos_devueltos = sum(
                lineas_articulo.filtered(
                    lambda l, p=prod_id: l.product_id.id == p and l.tipo == "devolucion"
                ).mapped("cantidad")
            )
            envases_recuperados = sum(
                lineas_envase.filtered(
                    lambda l, p=prod_id: l.product_id.id == p and l.tipo == "devolucion"
                ).mapped("cantidad")
            )

            disponibles = max(0, salida_llenos - entregados)
            vacios = salida_vacios + envases_recuperados
            fallados = salida_fallados + articulos_devueltos
            total_envases = disponibles + vacios + fallados

            productos_stock.append(
                {
                    "product_id": prod_id,
                    "nombre": linea.product_id.name,
                    "disponibles": disponibles,
                    "vacios": vacios,
                    "fallados": fallados,
                    "total_envases": total_envases,
                    "imagen": None,
                }
            )

        return self._json_response({"productos": productos_stock})

    @http.route(
        "/insa/api/control-playa",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def registrar_control_playa(self, **kw):
        """Paso 1.6: crea o actualiza (upsert por tipo, ver
        hoja_ruta_tipo_unique en insa.control.playa) el control de playa
        de salida o entrada de la hoja de ruta del día.

        Gateo de estado: salida se acepta en 'borrador' (crearla con
        líneas es lo que dispara el pase a 'en_reparto', ver
        _maybe_iniciar_reparto) o ya en 'en_reparto'; entrada solo en
        'en_reparto' (no antes de que arranque el reparto, no después de
        action_registrar_caja)."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        tipo = body.get("tipo")
        if tipo not in ("salida", "entrada"):
            return self._json_response(
                {"error": "'tipo' debe ser 'salida' o 'entrada'."}, status=400
            )

        date_str = body.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        Dayroute = request.env["fsm.route.dayroute"].sudo()
        hoja = Dayroute.search(
            [("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1
        )
        if not hoja:
            return self._json_response(
                {"error": "No hay hoja de ruta para esta ruta en esa fecha."},
                status=404,
            )

        estados_validos = {
            "salida": ("borrador", "en_reparto"),
            "entrada": ("en_reparto",),
        }
        if hoja.state not in estados_validos[tipo]:
            return self._json_response(
                {
                    "error": "No se puede registrar el control de playa de "
                    f"{tipo} con la hoja de ruta en estado '{hoja.state}'."
                },
                status=400,
            )

        control = hoja.control_playa_ids.filtered(lambda c, t=tipo: c.tipo == t)[:1]
        vals = {
            "hoja_ruta_id": hoja.id,
            "tipo": tipo,
            "kilometros": body.get("kilometros") or 0.0,
        }

        try:
            if control:
                control.write(vals)
            else:
                control = request.env["insa.control.playa"].sudo().create(vals)

            if "lineas" in body:
                # Revisión 06/10: la app manda la lista plana de productos
                # que el repartidor ve en pantalla, sin a_cargar_pedidos
                # (eso es la referencia del pedido habitual, calculada
                # solo en el backoffice al precargar). Antes de borrar y
                # recrear, se guarda esa referencia por producto para no
                # perderla en cada guardado de la app — si no, la
                # validación de "no cargar menos de lo que piden los
                # clientes" (_check_salida_cubre_pedidos) quedaría sin
                # efecto apenas la app tocara algo.
                a_cargar_previos = {
                    linea.product_id.id: linea.a_cargar_pedidos
                    for linea in control.linea_ids
                }
                control.linea_ids.unlink()
                for linea in body.get("lineas") or []:
                    request.env["insa.control.playa.linea"].sudo().create(
                        {
                            "control_playa_id": control.id,
                            "product_id": linea.get("product_id"),
                            # C-05: tres cantidades por producto. La app
                            # manda cantidad_fallada desde la pantalla del
                            # control; si no la manda (versión vieja), va
                            # en 0 y el conteo sigue siendo válido.
                            "cantidad": linea.get("cantidad", 0.0),
                            "cantidad_vacia": linea.get("cantidad_vacia", 0.0),
                            "cantidad_fallada": linea.get("cantidad_fallada", 0.0),
                            "a_cargar_pedidos": (
                                linea.get("a_cargar_pedidos")
                                if linea.get("a_cargar_pedidos") is not None
                                else a_cargar_previos.get(linea.get("product_id"), 0.0)
                            ),
                        }
                    )
        except (UserError, ValidationError) as e:
            return self._json_response({"error": str(e)}, status=400)

        return self._json_response(self._serializar_control_playa(control))

    def _serializar_control_playa(self, control):
        return {
            "id": control.id,
            "tipo": control.tipo,
            "state": control.state,
            "fecha_hora": control.fecha_hora.isoformat() if control.fecha_hora else None,
            "kilometros": control.kilometros,
            "aprobado": control.state == "confirmado",
            "aprobado_por": control.aprobado_por_id.name or None,
            "aprobado_fecha": (
                control.aprobado_fecha.isoformat() if control.aprobado_fecha else None
            ),
            "lineas": [
                {
                    "product_id": linea.product_id.id,
                    "producto": linea.product_id.name,
                    "cantidad": linea.cantidad,
                    "cantidad_vacia": linea.cantidad_vacia,
                    "cantidad_fallada": linea.cantidad_fallada,
                    "a_cargar_pedidos": linea.a_cargar_pedidos,
                }
                for linea in control.linea_ids
            ],
        }

    @http.route(
        "/insa/api/control-playa/aprobar",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def aprobar_control_playa(self, **kw):
        """C-08/RN-22, confirmado en la reunión del 08/06 (antes era el
        supuesto 0.1, 🔴 riesgo alto). Flujo real, textual: "los chicos
        hacen el control ellos de playa para ganar tiempo, le dan el
        celular al controlador… cuando va a aceptar, el sistema pide el
        usuario y la contraseña del controlador".

        Segundo factor de aprobación: requiere el Bearer token de la ruta
        (primer factor, ya lo exige _require_credencial, "este dispositivo
        es del reparto tal") MÁS login/password de un usuario real de
        res.users con el grupo group_controlador (segundo factor, "esta
        persona es controladora"). No cierra la sesión del repartidor:
        insa.repartidor.credencial sigue siendo, sin cambios, su mecanismo
        de acceso a la app.
        """
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        tipo = body.get("tipo")
        login = body.get("login")
        password = body.get("password")
        if tipo not in ("salida", "entrada") or not login or not password:
            return self._json_response(
                {"error": "Faltan 'tipo' ('salida'/'entrada'), 'login' o 'password'."},
                status=400,
            )

        date_str = body.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        hoja = (
            request.env["fsm.route.dayroute"]
            .sudo()
            .search([("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1)
        )
        if not hoja:
            return self._json_response(
                {"error": "No hay hoja de ruta para esta ruta en esa fecha."},
                status=404,
            )

        control = hoja.control_playa_ids.filtered(lambda c, t=tipo: c.tipo == t)[:1]
        if not control:
            return self._json_response(
                {"error": f"No hay control de playa de {tipo} para aprobar."},
                status=404,
            )

        # C-08: la lógica vive en el modelo (action_aprobar), no acá — el
        # wizard del backoffice (C-09) entra por el mismo camino, así que
        # no hay dos versiones de la regla que se puedan desincronizar.
        try:
            control.sudo().action_aprobar(login, password)
        except UserError as e:
            return self._json_response({"error": str(e)}, status=400)

        return self._json_response(self._serializar_control_playa(control))

    @http.route(
        "/insa/api/liquidacion",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def liquidacion(self, **kw):
        """Paso 1.9: lectura de la liquidación de la hoja de ruta del día.
        Solo lectura — los insa.liquidacion.articulo/.envase se calculan
        server-side (action_recalcular_liquidacion, disparado automático
        al pasar a 'consolidando' vía action_registrar_caja), la app no
        escribe nada acá. El color (supuesto 0.4: texto rojo, no relleno)
        es puramente visual y queda del lado de la app (Paso 2.1) — este
        endpoint solo expone los números y el signo de la diferencia."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        date_str = kw.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        hoja = (
            request.env["fsm.route.dayroute"]
            .sudo()
            .search([("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1)
        )
        if not hoja:
            return self._json_response(
                {
                    "hoja_ruta": None,
                    "disponible": False,
                    "tiene_diferencias": False,
                    "efectivo": None,
                    "articulos": [],
                    "envases": [],
                    "fallados": [],
                }
            )

        # RN-23 (C-08): la liquidación no arroja resultados hasta que el
        # control de entrada esté aprobado. Antes alcanzaba con que la
        # hoja estuviera en consolidando; ahora eso ya implica la
        # aprobación (action_registrar_caja la exige), pero se chequea
        # explícito para que la app pueda mostrar el motivo correcto.
        disponible = hoja.state in ("consolidando", "cerrada") and hoja.control_entrada_aprobado
        return self._json_response(
            {
                "hoja_ruta": {
                    "id": hoja.id,
                    "date": str(hoja.date),
                    "state": hoja.state,
                },
                "disponible": disponible,
                "control_entrada_aprobado": hoja.control_entrada_aprobado,
                "tiene_diferencias": hoja.tiene_diferencias,
                "tiene_discordancia_fallados": hoja.tiene_discordancia_fallados,
                "efectivo": {
                    "total_registrado": hoja.total_efectivo_registrado,
                    "efectivo_caja_repartidor": hoja.efectivo_caja_repartidor,
                    "diferencia": hoja.diferencia_efectivo,
                },
                "articulos": [
                    {
                        "product_id": linea.product_id.id,
                        "producto": linea.product_id.name,
                        # C-16: las cinco columnas del cuadro tal como las
                        # lee INSA, no solo el resultado.
                        "salida": linea.salida,
                        "entrada": linea.entrada,
                        "teorico": linea.teorico,
                        "registrado": linea.registrado,
                        "diferencia": linea.diferencia,
                    }
                    for linea in hoja.liquidacion_articulo_ids
                ],
                "envases": [
                    {
                        "product_id": linea.product_id.id,
                        "producto": linea.product_id.name,
                        "salida": linea.salida,
                        "prestamos": linea.prestamos,
                        "devoluciones": linea.devoluciones,
                        "entrada_teorica": linea.entrada_teorica,
                        "entrada_real": linea.entrada_real,
                        "diferencia": linea.diferencia,
                    }
                    for linea in hoja.liquidacion_envase_ids
                ],
                # C-07: el tercer cuadro. discordancia != 0 es la alerta
                # "hay discordancia entre la información del distribuidor
                # y el control de playa".
                "fallados": [
                    {
                        "product_id": linea.product_id.id,
                        "producto": linea.product_id.name,
                        "registrados": linea.registrados,
                        "contados": linea.contados,
                        "discordancia": linea.discordancia,
                    }
                    for linea in hoja.liquidacion_fallado_ids
                ],
            }
        )

    # -------------------------------------------------------------------------
    # Reordenamiento de paradas / clientes en la hoja de ruta
    # -------------------------------------------------------------------------

    @http.route(
        "/insa/api/reordenar-clientes",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def reordenar_clientes(self, **kw):
        """Persiste el orden personalizado de visita definido por el repartidor
        actualizando el campo `sequence` de cada `fsm.order` en la hoja de ruta.

        Body JSON:
          {
            "date": "YYYY-MM-DD",
            "partner_ids": [10, 25, 4, ...]
          }
        """
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        date_str = body.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        if fecha != date.today():
            return self._json_response(
                {
                    "error": "Solo se puede reordenar la hoja de ruta del día actual (modo solo lectura para otras fechas)."
                },
                status=403,
            )

        partner_ids = body.get("partner_ids")
        if not isinstance(partner_ids, list):
            return self._json_response(
                {"error": "El campo 'partner_ids' debe ser una lista de IDs de clientes."},
                status=400,
            )

        Dayroute = request.env["fsm.route.dayroute"].sudo()
        hoja = Dayroute.search(
            [("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1
        )
        if not hoja:
            return self._json_response(
                {"error": "No existe hoja de ruta para la fecha y móvil indicados."},
                status=404,
            )

        # Mapear cada partner_id con su nueva posición (1-indexed para sequence)
        partner_rank = {pid: idx + 1 for idx, pid in enumerate(partner_ids)}

        # Asignar la secuencia correspondiente a cada fsm.order de la hoja
        for order in hoja.order_ids:
            partner = order.location_id.partner_id
            if partner and partner.id in partner_rank:
                order.sequence = partner_rank[partner.id]

        return self._json_response({"ok": True})

    # -------------------------------------------------------------------------
    # Búsqueda de clientes activos y Activación por derecho propio (App móvil)
    # -------------------------------------------------------------------------

    @http.route(
        "/insa/api/clientes/buscar-activos",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def buscar_clientes_activos(self, **kw):
        """Endpoint exclusivo para la app móvil: busca clientes activos
        (active=True, customer_rank > 0) por nombre o dirección para la
        pantalla Todos y la prevención de duplicados en Alta de Cliente."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        q = (kw.get("q") or "").strip()
        if len(q) < 2:
            return self._json_response({"clientes": []})

        Partner = request.env["res.partner"].sudo()
        domain = [
            ("customer_rank", ">", 0),
            ("active", "=", True),
            "|",
            "|",
            ("name", "ilike", q),
            ("street", "ilike", q),
            ("insa_city_id.name", "ilike", q),
        ]
        if "x_insa_estado_ws" in Partner._fields:
            domain.append(("x_insa_estado_ws", "!=", "baja"))

        partners = Partner.search(domain, limit=20)
        return self._json_response(
            {
                "clientes": [
                    {
                        "partner_id": p.id,
                        "nombre": p.name,
                        "direccion": ", ".join(filter(None, [p.street, p.city])),
                        "saldo_facturas": p.credit,
                        "consumos": p.total_invoiced,
                        "es_cliente_propio": getattr(p, "es_cliente_propio", False),
                        "fecha_ultima_entrega": (
                            p.fecha_ultima_entrega.isoformat()
                            if p.fecha_ultima_entrega
                            else None
                        ),
                        "fecha_ultima_cobranza": (
                            p.fecha_ultima_cobranza.isoformat()
                            if p.fecha_ultima_cobranza
                            else None
                        ),
                        "fue_vencido": bool(getattr(p, "fue_vencido", False)),
                        "fuc_vencido": bool(getattr(p, "fuc_vencido", False)),
                        "al_dia": bool(not getattr(p, "fue_vencido", False) and not getattr(p, "fuc_vencido", False)),
                        "pedido_habitual": self._pedido_habitual(p),
                        "saldos_envase": self._saldos_envase(p),
                    }
                    for p in partners
                ]
            }
        )

    @http.route(
        "/insa/api/clientes/activar-propio",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
    )
    def activar_cliente_propio(self, **kw):
        """Endpoint exclusivo para la app móvil: habilita a una persona física
        como cliente por derecho propio (Caso 3 / insa_autorizacion_pedido),
        registrando su domicilio particular y creando su fsm.location propia."""
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        body = self._get_body()
        partner_id = body.get("partner_id")
        if not partner_id:
            return self._json_response(
                {"error": "El campo 'partner_id' es obligatorio."}, status=400
            )

        Partner = request.env["res.partner"].sudo()
        persona = Partner.browse(partner_id)
        if not persona.exists():
            return self._json_response(
                {"error": "El contacto especificado no existe."}, status=404
            )

        if persona.is_company:
            return self._json_response(
                {"error": "Solo una persona física puede ser cliente por derecho propio."},
                status=400,
            )

        if getattr(persona, "es_cliente_propio", False):
            return self._json_response(
                {"error": "El contacto ya es cliente por derecho propio."},
                status=400,
            )

        city_id = body.get("city_id")
        street_id = body.get("street_id")
        street_number = (body.get("street_number") or "").strip()
        floor = (body.get("floor") or "").strip()
        apartment = (body.get("apartment") or "").strip()
        zip_code = (body.get("zip") or "").strip()
        notes = (body.get("notes") or "").strip()

        if not city_id or not street_id or not street_number:
            return self._json_response(
                {"error": "Ciudad, calle y número son obligatorios."},
                status=400,
            )

        city = request.env["insa.city"].sudo().browse(city_id)
        street = request.env["insa.street"].sudo().browse(street_id)
        if not city.exists() or not street.exists():
            return self._json_response(
                {"error": "Ciudad o calle inválida."}, status=400
            )

        # Construir partes del domicilio
        partes = [street.name, street_number]
        if floor:
            partes.append("Piso %s" % floor)
        if apartment:
            partes.append("Dto %s" % apartment)
        domicilio_texto = " ".join(partes)

        persona_values = {
            "insa_city_id": city.id,
            "insa_street_id": street.id,
            "insa_street_number": street_number,
            "insa_floor": floor or False,
            "insa_apartment": apartment or False,
            "zip": zip_code or persona.zip,
            "insa_address_notes": notes or False,
            "customer_rank": max(persona.customer_rank, 1),
        }
        if "es_cliente_propio" in Partner._fields:
            persona_values["es_cliente_propio"] = True

        persona.write(persona_values)

        # Crear domicilio como entidad separada y fsm.location dedicada
        domicilio = Partner.create(
            {
                "name": "%s (consumo propio)" % persona.name,
                "parent_id": (persona.parent_id or persona).id,
                "type": "other",
                "street": domicilio_texto,
                "street2": city.name or False,
                "city": persona.city or False,
                "state_id": persona.state_id.id if persona.state_id else False,
                "country_id": persona.country_id.id if persona.country_id else False,
                "zip": zip_code or persona.zip,
            }
        )

        location_values = {
            "partner_id": domicilio.id,
            "owner_id": persona.id,
            "fsm_route_id": cred.route_id.id,
            "notes": notes or False,
        }
        location = request.env["fsm.location"].sudo().create(location_values)

        if "fsm_location_propia_id" in Partner._fields:
            persona.fsm_location_propia_id = location.id

        return self._json_response(
            {
                "partner_id": persona.id,
                "nombre": persona.name,
                "direccion": ", ".join(filter(None, [domicilio_texto, city.name])),
                "saldo_facturas": persona.credit,
                "consumos": persona.total_invoiced,
                "es_cliente_propio": True,
            }
        )

    @http.route(
        "/insa/api/stock-en-ruta",
        type="http",
        auth="public",
        methods=["GET"],
        csrf=False,
    )
    def stock_en_ruta(self, **kw):
        """Devuelve el stock actual en el camión a partir de lo cargado en la
        Salida del Control de Playa menos las entregas registradas en las visitas
        del día.
        """
        cred = self._require_credencial()
        if not cred:
            return self._json_response({"error": "No autorizado."}, status=401)

        date_str = kw.get("date") or date.today().isoformat()
        try:
            fecha = date.fromisoformat(date_str)
        except ValueError:
            return self._json_response(
                {"error": "Parámetro 'date' inválido, formato esperado YYYY-MM-DD."},
                status=400,
            )

        hoja = (
            request.env["fsm.route.dayroute"]
            .sudo()
            .search([("route_id", "=", cred.route_id.id), ("date", "=", fecha)], limit=1)
        )
        if not hoja:
            return self._json_response({"productos": []})

        control_salida = hoja.control_playa_ids.filtered(lambda c: c.tipo == "salida")[:1]
        productos_dict = {}

        if control_salida:
            for linea in control_salida.linea_ids:
                p = linea.product_id
                productos_dict[p.id] = {
                    "product": p,
                    "salida_llenos": linea.cantidad,
                }

        # Sumar ventas, devoluciones y envases de las visitas registradas
        entregas = {}
        fallados = {}
        vacios = {}

        for mov in hoja.movimiento_cliente_ids:
            for la in mov.linea_articulo_ids:
                pid = la.product_id.id
                if pid not in productos_dict:
                    productos_dict[pid] = {"product": la.product_id, "salida_llenos": 0.0}
                if la.tipo == "venta":
                    entregas[pid] = entregas.get(pid, 0.0) + la.cantidad
                elif la.tipo == "devolucion":
                    fallados[pid] = fallados.get(pid, 0.0) + la.cantidad
                    if getattr(la, "cambio_directo", True):
                        entregas[pid] = entregas.get(pid, 0.0) + la.cantidad

            for le in mov.linea_envase_ids:
                epid = le.product_id.id
                if le.tipo == "devolucion":
                    vacios[epid] = vacios.get(epid, 0.0) + le.cantidad

        res = []
        for pid, pdata in productos_dict.items():
            prod = pdata["product"]
            salida = int(pdata["salida_llenos"])
            entregado = int(entregas.get(pid, 0.0))
            disponibles = salida - entregado

            envase_id = prod.envase_id.id if getattr(prod, "envase_id", False) else pid
            cant_vacios = int(vacios.get(envase_id, vacios.get(pid, 0.0)))
            cant_fallados = int(fallados.get(pid, 0.0))
            total_envases = disponibles + cant_vacios + cant_fallados

            imagen_b64 = None
            if prod.image_128:
                try:
                    imagen_b64 = f"data:image/png;base64,{prod.image_128.decode('utf-8')}"
                except Exception:
                    imagen_b64 = None

            res.append({
                "product_id": prod.id,
                "nombre": prod.name,
                "disponibles": disponibles,
                "vacios": cant_vacios,
                "fallados": cant_fallados,
                "total_envases": total_envases,
                "imagen": imagen_b64,
            })

        return self._json_response({"productos": res})

