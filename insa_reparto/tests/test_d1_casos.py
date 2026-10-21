# Copyright 2026 Olpa Group
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl.html)
"""Los 7 casos de test obligatorios del documento D1 (§10 /
Hoja_de_Ruta_Flujo_Punta_a_Punta.md, sección 7 del prompt de
implementación), tomados textualmente de la reunión del 18/07/2026.

Nota: esta suite se escribió sin una instancia de Odoo corriendo en la
sesión de desarrollo — no se ejecutó. Correr con:

    odoo-bin -d <db> -i insa_reparto --test-enable --stop-after-init \
        --test-tags insa_reparto

antes de dar por buena la entrega.
"""
from datetime import date

from lxml import etree

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestD1Casos(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # fsm.route._default_team_id revienta si no hay ningún fsm.team.
        if not cls.env["fsm.team"].search([], limit=1):
            cls.env["fsm.team"].create({"name": "Equipo Test D1"})

        cls.envase_sifon = cls._crear_envase("Envase Sifón 1 1/4")
        cls.sifon = cls._crear_articulo("Sifón 1 1/4", cls.envase_sifon)

        cls.envase_bidon15 = cls._crear_envase("Envase Bidón 15")
        cls.bidon15 = cls._crear_articulo("Bidón de 15", cls.envase_bidon15)

        cls.envase_bidon20 = cls._crear_envase("Envase Bidón 20")
        cls.bidon20_comun = cls._crear_articulo("Bidón de 20 común", cls.envase_bidon20)
        cls.bidon20_canilla = cls._crear_articulo(
            "Bidón de 20 con canilla", cls.envase_bidon20
        )

        # C-08: desde el 08/06 ningún control de playa vale sin la
        # aprobación de un controlador. Toda la suite necesita uno.
        cls.controlador = cls.env["res.users"].create(
            {
                "name": "Controlador Test",
                "login": "controlador.test.d1",
                "groups_id": [
                    (4, cls.env.ref("insa_reparto.group_controlador").id),
                ],
            }
        )
        cls.repartidor_user = cls.env["res.users"].create(
            {
                "name": "Repartidor Test",
                "login": "repartidor.test.d1",
                "groups_id": [(4, cls.env.ref("fieldservice.group_fsm_user").id)],
            }
        )
        # R-03 (reunión 14/09): declarar el efectivo y finalizar son de la
        # administrativa de cierre y de nadie más.
        cls.camila = cls.env["res.users"].create(
            {
                "name": "Camila Test",
                "login": "camila.test.d1",
                "groups_id": [
                    (4, cls.env.ref("insa_reparto.group_administrativa_cierre").id),
                ],
            }
        )

    @classmethod
    def _crear_envase(cls, name):
        return cls.env["product.template"].create(
            {"name": name, "es_envase": True, "is_storable": True}
        )

    @classmethod
    def _crear_articulo(cls, name, envase):
        return cls.env["product.template"].create(
            {
                "name": name,
                "es_retornable": True,
                "envase_id": envase.id,
                "is_storable": True,
            }
        )

    def _crear_cliente(self, name):
        # type=fsm_location dispara la creación automática de la
        # ubicación de stock propia del cliente (res_partner.py) y del
        # fsm.location asociado (OCA fieldservice).
        return self.env["res.partner"].create({"name": name, "type": "fsm_location"})

    def _crear_dayroute(self, numero_reparto):
        # numero_reparto >= 900 a propósito: INSA usa 1-17 en la vida real
        # (y en cualquier dato de demo/prueba que se cargue a mano), así que
        # el rango 900+ evita chocar con el unique constraint de fsm.route.
        route = self.env["fsm.route"].create(
            {
                "name": "Ruta test %s" % numero_reparto,
                "max_order": 50,
                "numero_reparto": numero_reparto,
            }
        )
        fecha = date.today()
        day = self.env.ref("fieldservice_route.fsm_route_day_%d" % fecha.weekday())
        route.day_ids = [(6, 0, [day.id])]
        return self.env["fsm.route.dayroute"].create(
            {"route_id": route.id, "date": fecha}
        )

    def _control_playa(self, dayroute, tipo, lineas, aprobar=True):
        # Revisión 02/10: el control de SALIDA ahora se crea solo al
        # crear la hoja de ruta (unificado con la pestaña Pedidos, ver
        # create() en fsm.route.dayroute) — unique(hoja_ruta_id, tipo)
        # impide crear un segundo, así que acá se reusa el existente en
        # vez de crear uno nuevo.
        control = dayroute.control_salida_ids if tipo == "salida" else self.env["insa.control.playa"]
        if control:
            control.write({"linea_ids": [(0, 0, linea) for linea in lineas]})
        else:
            control = self.env["insa.control.playa"].create(
                {
                    "hoja_ruta_id": dayroute.id,
                    "tipo": tipo,
                    "linea_ids": [(0, 0, linea) for linea in lineas],
                }
            )
        # RN-23: cargar el control ya no alcanza — hasta que un
        # controlador no lo aprueba, la hoja no sale a la calle ni la
        # liquidación arroja resultados.
        if aprobar:
            control._aprobar_como(self.controlador)
        return control

    def test_ct01_ct02_liquidacion_articulos(self):
        """CT-01 (sin diferencia) y CT-02 (con diferencia), tal como
        aparecen juntos en el ejemplo real de la reunión (Reparto 2,
        día martes)."""
        dayroute = self._crear_dayroute(901)

        self._control_playa(
            dayroute,
            "salida",
            [
                {"product_id": self.sifon.id, "cantidad": 150},
                {"product_id": self.bidon15.id, "cantidad": 42},
            ],
        )
        self.assertEqual(dayroute.state, "en_reparto")  # RN-01 / RN-23

        self._control_playa(
            dayroute,
            "entrada",
            [
                {"product_id": self.sifon.id, "cantidad": 63},
                {"product_id": self.bidon15.id, "cantidad": 9},
            ],
        )

        cliente = self._crear_cliente("Cliente CT01-CT02")
        self.env["insa.movimiento.cliente"].create(
            {
                "hoja_ruta_id": dayroute.id,
                "partner_id": cliente.id,
                "linea_articulo_ids": [
                    (0, 0, {"product_id": self.sifon.id, "cantidad": 87, "precio_unitario": 100}),
                    (0, 0, {"product_id": self.bidon15.id, "cantidad": 27, "precio_unitario": 100}),
                ],
            }
        )

        dayroute.action_recalcular_liquidacion()

        liq_sifon = dayroute.liquidacion_articulo_ids.filtered(lambda l: l.product_id == self.sifon)
        liq_bidon = dayroute.liquidacion_articulo_ids.filtered(lambda l: l.product_id == self.bidon15)

        # CT-01 — sin diferencia
        self.assertEqual(liq_sifon.teorico, 87)
        self.assertEqual(liq_sifon.registrado, 87)
        self.assertEqual(liq_sifon.diferencia, 0)

        # CT-02 — con diferencia
        self.assertEqual(liq_bidon.teorico, 33)
        self.assertEqual(liq_bidon.registrado, 27)
        self.assertEqual(liq_bidon.diferencia, 6)

    def test_ct03_venta_simple(self):
        """Galván, Brenda: 3 bidones de 20 con canilla, sin cobro →
        íconos ✔️🛒, sin ícono de envase ni de pesos."""
        dayroute = self._crear_dayroute(902)
        self._control_playa(dayroute, "salida", [{"product_id": self.bidon20_canilla.id, "cantidad": 10}])

        cliente = self._crear_cliente("Galván, Brenda")
        mov = self.env["insa.movimiento.cliente"].create(
            {
                "hoja_ruta_id": dayroute.id,
                "partner_id": cliente.id,
                "linea_articulo_ids": [
                    (0, 0, {"product_id": self.bidon20_canilla.id, "cantidad": 3, "precio_unitario": 500})
                ],
            }
        )

        self.assertTrue(mov.visitado)
        self.assertTrue(mov.tiene_venta)
        self.assertFalse(mov.tiene_mov_envase)
        self.assertFalse(mov.tiene_cobro)
        self.assertEqual(mov.iconos_visita, "✔️ 🛒")

    def test_ct04_venta_mas_prestamo(self):
        """Sosa, Mario: 2 bidones de 20 vendidos + 2 prestados →
        íconos ✔️🛒➡️; el cuadro de envases baja la entrada teórica en 2."""
        dayroute = self._crear_dayroute(903)
        self._control_playa(dayroute, "salida", [{"product_id": self.bidon20_comun.id, "cantidad": 10}])

        cliente = self._crear_cliente("Sosa, Mario")
        mov = self.env["insa.movimiento.cliente"].create(
            {
                "hoja_ruta_id": dayroute.id,
                "partner_id": cliente.id,
                "linea_articulo_ids": [
                    (0, 0, {"product_id": self.bidon20_comun.id, "cantidad": 2, "precio_unitario": 500})
                ],
                "linea_envase_ids": [
                    (0, 0, {"product_id": self.bidon20_comun.id, "tipo": "prestamo", "cantidad": 2})
                ],
            }
        )

        self.assertTrue(mov.tiene_venta)
        self.assertTrue(mov.tiene_mov_envase)
        self.assertEqual(mov.iconos_visita, "✔️ 🛒 ➡️")

        self._control_playa(dayroute, "entrada", [{"product_id": self.bidon20_comun.id, "cantidad": 8}])
        dayroute.action_recalcular_liquidacion()

        liq_envase = dayroute.liquidacion_envase_ids.filtered(lambda l: l.product_id == self.bidon20_comun)
        # entrada_teorica = salida(10) - prestamos(2) + devoluciones(0) = 8
        self.assertEqual(liq_envase.entrada_teorica, 8)

    def test_ct05_deuda_envases(self):
        """Banco de la Nación Argentina · bidón de 20 común · consumo
        habitual 3 · saldo 10 → deuda = 7.

        Nota: el checkpoint del 06/08/2026 decidió mantener el campo
        tolerancia_envase (%) en vez de pasar a consumo habitual exacto
        (RN-16). Para reproducir el número literal de la reunión, acá
        se fuerza tolerancia = 0% en el producto de test — es el
        comportamiento que da permitido = consumo habitual exacto."""
        cliente = self._crear_cliente("Banco de la Nación Argentina")
        self.bidon20_comun.tolerancia_envase = 0.0

        camion = self.env["stock.warehouse"].search([], limit=1).lot_stock_id
        self.env["stock.quant"]._update_available_quantity(
            self.envase_bidon20.product_variant_id, cliente.property_stock_customer, 10
        )
        # el quant nace también en el camión por la búsqueda anterior en
        # algunas versiones; no afecta el cálculo, que solo mira la
        # ubicación del cliente.
        self.assertTrue(camion)

        saldo = self.env["insa.saldo.envase"].create(
            {
                "partner_id": cliente.id,
                "product_id": self.bidon20_comun.id,
                "consumo_promedio": 3,
            }
        )

        self.assertEqual(saldo.saldo, 10)
        self.assertEqual(saldo.permitido, 3)
        self.assertEqual(saldo.cantidad_a_recuperar, 7)

    def test_ct06_salida_entrada_prestamo_imputado(self):
        """Distribuidor 9 (Matías Jaime): sale con 20 envases llenos,
        vuelve con 15 llenos → vendió 5 · de esos, trae 3 vacíos y 2
        no → 2 envases quedaron prestados, imputados a Nicolás
        Alessandrini, cuyo saldo sube en 2."""
        dayroute = self._crear_dayroute(904)
        self._control_playa(dayroute, "salida", [{"product_id": self.bidon20_comun.id, "cantidad": 20}])

        nicolas = self._crear_cliente("Nicolás Alessandrini")
        mov = self.env["insa.movimiento.cliente"].create(
            {
                "hoja_ruta_id": dayroute.id,
                "partner_id": nicolas.id,
                "linea_envase_ids": [
                    (0, 0, {"product_id": self.bidon20_comun.id, "tipo": "prestamo", "cantidad": 2})
                ],
            }
        )
        mov.action_confirmar()
        self.assertTrue(mov.envase_movido)

        self._control_playa(
            dayroute, "entrada", [{"product_id": self.bidon20_comun.id, "cantidad": 15, "cantidad_vacia": 3}]
        )
        dayroute.action_recalcular_liquidacion()

        liq = dayroute.liquidacion_envase_ids.filtered(lambda l: l.product_id == self.bidon20_comun)
        self.assertEqual(liq.entrada_teorica, 18)  # 20 - 2 préstamos + 0 devoluciones
        self.assertEqual(liq.entrada_real, 18)  # 15 llenos + 3 vacíos
        self.assertEqual(liq.diferencia, 0)  # conciliado: el préstamo explica la diferencia

        saldo = self.env["insa.saldo.envase"].create(
            {"partner_id": nicolas.id, "product_id": self.bidon20_comun.id}
        )
        self.assertEqual(saldo.saldo, 2)

    def test_ct07_cierre_con_diferencia(self):
        """Reparto con diferencia de 6 bidones sin resolver → nadie más que
        la administrativa de cierre finaliza (R-03/R-07); cada diferencia
        exige su motivo y queda en el diario de pérdidas; queda registrado
        observación/usuario/fecha y se genera el ajuste de inventario."""
        dayroute = self._crear_dayroute(905)
        self._control_playa(dayroute, "salida", [{"product_id": self.bidon15.id, "cantidad": 42}])

        cliente = self._crear_cliente("Cliente CT07")
        self.env["insa.movimiento.cliente"].create(
            {
                "hoja_ruta_id": dayroute.id,
                "partner_id": cliente.id,
                "linea_articulo_ids": [
                    (0, 0, {"product_id": self.bidon15.id, "cantidad": 27, "precio_unitario": 100})
                ],
            }
        )
        self._control_playa(dayroute, "entrada", [{"product_id": self.bidon15.id, "cantidad": 9}])

        # ni el repartidor ni el supervisor declaran el efectivo
        with self.assertRaises(UserError):
            dayroute.with_user(self.repartidor_user)._declarar_efectivo([])
        dayroute.with_user(self.camila)._declarar_efectivo([])
        self.assertEqual(dayroute.state, "consolidando")
        self.assertTrue(dayroute.tiene_diferencias)

        # usuario común bloqueado para finalizar
        with self.assertRaises(UserError):
            dayroute.with_user(self.repartidor_user).action_finalizar_reparto()

        # sin el segundo control (4.7) tampoco se puede finalizar
        with self.assertRaises(UserError):
            dayroute.with_user(self.camila).action_finalizar_reparto()
        dayroute.with_user(self.camila)._verificar_efectivo_como(self.controlador, [])

        # con diferencias, "Finalizar" abre el cierre con diferencias
        accion = dayroute.with_user(self.camila).action_finalizar_reparto()
        self.assertEqual(accion["res_model"], "insa.cerrar.con.diferencias.wizard")
        self.assertEqual(dayroute.state, "consolidando")

        Wizard = self.env["insa.cerrar.con.diferencias.wizard"].with_user(self.camila)
        defaults = Wizard.with_context(default_hoja_ruta_id=dayroute.id).default_get(
            ["hoja_ruta_id", "linea_ids"]
        )
        # una línea por diferencia: los 6 bidones que faltan y sus envases
        # (volvieron 9 llenos y ningún vacío de los 42 que salieron)
        lineas = [comando[2] for comando in defaults["linea_ids"]]
        por_tipo = {linea["tipo"]: linea for linea in lineas}
        self.assertEqual(set(por_tipo), {"articulo", "envase"})
        self.assertEqual(por_tipo["articulo"]["cantidad"], 6)
        self.assertEqual(por_tipo["envase"]["cantidad"], 33)
        self.assertEqual(
            por_tipo["envase"]["motivo_id"],
            self.env.ref("insa_reparto.motivo_perdida_envase_no_recuperado").id,
        )

        observacion = "Se acepta la diferencia, revisado por cámaras."
        # sin motivo (el artículo no trae uno sugerido) no se finaliza
        with self.assertRaises(UserError):
            dayroute.with_user(self.camila)._finalizar_con_diferencias(lineas, observacion)
        motivo = self.env.ref("insa_reparto.motivo_perdida_mercaderia_en_ruta")
        wizard = Wizard.create(
            {
                "hoja_ruta_id": dayroute.id,
                "motivo": observacion,
                "linea_ids": [
                    (0, 0, dict(linea, motivo_id=linea.get("motivo_id") or motivo.id))
                    for linea in lineas
                ],
            }
        )
        wizard.action_confirmar()

        self.assertEqual(dayroute.state, "cerrada")
        self.assertTrue(dayroute.close_forced)
        self.assertEqual(dayroute.close_forced_reason, observacion)
        self.assertEqual(dayroute.close_forced_by, self.camila)
        self.assertTrue(dayroute.close_forced_date)

        self.assertEqual(len(dayroute.perdida_ids), 2)
        perdida_articulo = dayroute.perdida_ids.filtered(lambda p: p.tipo == "articulo")
        self.assertEqual(perdida_articulo.motivo_id, motivo)
        self.assertEqual(perdida_articulo.cantidad, 6)
        self.assertEqual(perdida_articulo.observacion, observacion)
        self.assertEqual(perdida_articulo.route_id, dayroute.route_id)

        ajuste_articulo = dayroute.ajuste_inventario_ids.filtered(lambda a: a.tipo == "articulo")
        self.assertTrue(ajuste_articulo)
        self.assertEqual(ajuste_articulo.diferencia, 6)
        self.assertEqual(ajuste_articulo.motivo, observacion)

        # la hoja de ruta queda readonly (§5.7), y sus visitas también
        with self.assertRaises(UserError):
            dayroute.write({"repartidor_id": False})
        with self.assertRaises(UserError):
            dayroute.movimiento_cliente_ids.write({"cobro_efectivo": 999})

    # ------------------------------------------------------------------
    # Casos nuevos de la reunión del 08/06 (Sección 1: C-05 a C-09)
    # ------------------------------------------------------------------

    def test_ctn1_control_salida_con_fallados_o_vacios(self):
        """CT-N1 — en el control de SALIDA, vacíos y fallados van siempre
        en 0 (RN-21). Los campos existen igual, para que el repartidor vea
        la misma pantalla en los dos momentos del día."""
        dayroute = self._crear_dayroute(906)

        with self.assertRaises(ValidationError):
            self._control_playa(
                dayroute,
                "salida",
                [{"product_id": self.sifon.id, "cantidad": 150, "cantidad_vacia": 2}],
                aprobar=False,
            )

        with self.assertRaises(ValidationError):
            self._control_playa(
                dayroute,
                "salida",
                [{"product_id": self.sifon.id, "cantidad": 150, "cantidad_fallada": 1}],
                aprobar=False,
            )

        # con los tres campos presentes y los dos que corresponden en 0,
        # guarda sin chistar
        control = self._control_playa(
            dayroute,
            "salida",
            [
                {
                    "product_id": self.sifon.id,
                    "cantidad": 150,
                    "cantidad_vacia": 0,
                    "cantidad_fallada": 0,
                }
            ],
        )
        self.assertEqual(control.state, "confirmado")
        self.assertEqual(dayroute.state, "en_reparto")

    def test_ctn2_cambio_directo_producto_no_conforme(self):
        """CT-N2 — el repartidor registra una devolución de 1 bidón de 20
        con canilla, motivo "pinchado", con cambio directo. Si el control
        de entrada cuenta ese bidón como fallado, la conciliación da 0. Si
        lo cuenta como vacío, salta la discordancia."""
        dayroute = self._crear_dayroute(907)
        self._control_playa(
            dayroute,
            "salida",
            [{"product_id": self.bidon20_canilla.id, "cantidad": 10}],
        )

        ana = self._crear_cliente("Ana CT-N2")
        self.env["insa.movimiento.cliente"].create(
            {
                "hoja_ruta_id": dayroute.id,
                "partner_id": ana.id,
                "linea_articulo_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": self.bidon20_canilla.id,
                            "tipo": "devolucion",
                            "motivo": "pinchado",
                            "cambio_directo": True,
                            "cantidad": 1,
                            "precio_unitario": 100,
                        },
                    )
                ],
            }
        )

        # bien contado: 9 llenos volvieron + 1 fallado
        self._control_playa(
            dayroute,
            "entrada",
            [
                {
                    "product_id": self.bidon20_canilla.id,
                    "cantidad": 9,
                    "cantidad_fallada": 1,
                }
            ],
        )
        dayroute.action_recalcular_liquidacion()

        fallado = dayroute.liquidacion_fallado_ids
        self.assertEqual(fallado.registrados, 1)
        self.assertEqual(fallado.contados, 1)
        self.assertEqual(fallado.discordancia, 0)
        self.assertFalse(dayroute.tiene_discordancia_fallados)

        # C-06 / H-1: el cambio directo no es venta pero sí salió un lleno
        # del camión, así que cuenta como registrado y la liquidación de
        # artículos cierra en 0.
        liq = dayroute.liquidacion_articulo_ids
        self.assertEqual(liq.teorico, 1)
        self.assertEqual(liq.registrado, 1)
        self.assertEqual(liq.diferencia, 0)
        # y no factura nada
        mov = dayroute.movimiento_cliente_ids
        self.assertEqual(mov.linea_articulo_ids.subtotal, 0.0)
        self.assertTrue(mov.tiene_devolucion_articulo)

        # mal contado: el mismo bidón cargado como vacío → discordancia
        entrada = dayroute.control_playa_ids.filtered(lambda c: c.tipo == "entrada")
        entrada.action_volver_a_borrador()
        entrada.linea_ids.write({"cantidad_fallada": 0, "cantidad_vacia": 1})
        entrada._aprobar_como(self.controlador)
        dayroute.action_recalcular_liquidacion()

        self.assertEqual(dayroute.liquidacion_fallado_ids.discordancia, 1)
        self.assertTrue(dayroute.tiene_discordancia_fallados)

    def test_ctn4_aprobacion_del_control_de_playa(self):
        """CT-N4 — el repartidor no puede aprobar su propio conteo; el
        controlador sí, y recién ahí la hoja sale a la calle (RN-22/23)."""
        dayroute = self._crear_dayroute(908)
        control = self._control_playa(
            dayroute,
            "salida",
            [{"product_id": self.sifon.id, "cantidad": 150}],
            aprobar=False,
        )

        self.assertEqual(control.state, "borrador")
        # R-01 (14/09): la hoja nace en reparto; lo que bloquea es la
        # falta de aprobación de la salida (R-09).
        self.assertEqual(dayroute.state, "en_reparto")
        self.assertFalse(dayroute.control_salida_aprobado)

        # sin control de salida aprobado no hay visitas (RN-01 / R-09)
        with self.assertRaises(UserError):
            self.env["insa.movimiento.cliente"].create(
                {
                    "hoja_ruta_id": dayroute.id,
                    "partner_id": self._crear_cliente("Cliente CT-N4").id,
                }
            )

        # el repartidor no tiene permiso de controlador
        with self.assertRaises(UserError):
            control._aprobar_como(self.repartidor_user)
        self.assertEqual(control.state, "borrador")

        control._aprobar_como(self.controlador)
        self.assertEqual(control.state, "confirmado")
        self.assertEqual(control.aprobado_por_id, self.controlador)
        self.assertTrue(control.aprobado_fecha)
        self.assertTrue(dayroute.control_salida_aprobado)

        # un control aprobado ya no se edita
        with self.assertRaises(UserError):
            control.linea_ids.write({"cantidad": 999})
        with self.assertRaises(UserError):
            control.write({"kilometros": 123})

    def test_ctn5_liquidacion_bloqueada_sin_entrada_aprobada(self):
        """CT-N5 — con el control de entrada cargado pero sin aprobar, la
        liquidación no cierra: "recién ahí la liquidación nos arroja cómo
        quedaron las ventas y las devoluciones"."""
        dayroute = self._crear_dayroute(909)
        self._control_playa(
            dayroute, "salida", [{"product_id": self.bidon15.id, "cantidad": 42}]
        )
        self._control_playa(
            dayroute,
            "entrada",
            [{"product_id": self.bidon15.id, "cantidad": 40}],
            aprobar=False,
        )

        self.assertFalse(dayroute.control_entrada_aprobado)
        self.assertFalse(dayroute.liquidacion_articulo_ids)
        with self.assertRaises(UserError):
            dayroute.with_user(self.camila)._declarar_efectivo([])
        self.assertEqual(dayroute.state, "en_reparto")

        entrada = dayroute.control_playa_ids.filtered(lambda c: c.tipo == "entrada")
        entrada._aprobar_como(self.controlador)

        # R-04: la liquidación es del controlador — queda calculada en el
        # momento en que aprueba el regreso.
        self.assertTrue(dayroute.control_entrada_aprobado)
        self.assertEqual(dayroute.liquidacion_articulo_ids.diferencia, 2)
        dayroute.with_user(self.camila)._declarar_efectivo([])
        self.assertEqual(dayroute.state, "consolidando")

    # ------------------------------------------------------------------
    # Reunión de avances del 14/09 (R-01 a R-08)
    # ------------------------------------------------------------------

    def _reparto_limpio(self, numero_reparto, cobro=1000.0):
        """Hoja sin diferencias: sale con 10 bidones de 20; un cliente
        compra 2, se queda con 2 envases prestados y paga en efectivo;
        vuelven 8 llenos."""
        dayroute = self._crear_dayroute(numero_reparto)
        self._control_playa(
            dayroute, "salida", [{"product_id": self.bidon20_comun.id, "cantidad": 10}]
        )
        cliente = self._crear_cliente("Cliente reparto %s" % numero_reparto)
        mov = self.env["insa.movimiento.cliente"].create(
            {
                "hoja_ruta_id": dayroute.id,
                "partner_id": cliente.id,
                "cobro_efectivo": cobro,
                "linea_articulo_ids": [
                    (0, 0, {"product_id": self.bidon20_comun.id, "cantidad": 2, "precio_unitario": 500})
                ],
                "linea_envase_ids": [
                    (0, 0, {"product_id": self.bidon20_comun.id, "tipo": "prestamo", "cantidad": 2})
                ],
            }
        )
        self._control_playa(
            dayroute, "entrada", [{"product_id": self.bidon20_comun.id, "cantidad": 8}]
        )
        return dayroute, cliente, mov

    def test_r02_finalizar_confirma_todo_en_bloque(self):
        """R-02 — criterio de aceptación de la reunión: con el reparto en
        curso, el consumo del cliente no aparece en su ficha; con el
        reparto cerrado, sí. Al finalizar: pedido de venta confirmado y sin
        facturar, saldo de envases y FUE/FUC."""
        dayroute, cliente, mov = self._reparto_limpio(910)
        saldo = self.env["insa.saldo.envase"].create(
            {"partner_id": cliente.id, "product_id": self.bidon20_comun.id}
        )

        dayroute.with_user(self.camila)._declarar_efectivo([(1000, 1)])
        self.assertEqual(dayroute.state, "consolidando")
        self.assertFalse(dayroute.tiene_diferencias)

        # reparto abierto: nada impactó en el cliente
        self.assertFalse(cliente.fecha_ultima_entrega)
        self.assertFalse(cliente.fecha_ultima_cobranza)
        self.assertFalse(mov.sale_order_id)
        self.assertFalse(mov.envase_movido)
        self.assertEqual(saldo.saldo, 0)

        dayroute.with_user(self.camila)._verificar_efectivo_como(self.controlador, [(1000, 1)])
        self.assertTrue(dayroute.with_user(self.camila).action_finalizar_reparto())
        self.assertEqual(dayroute.state, "cerrada")
        self.assertFalse(dayroute.close_forced)

        pedido = mov.sale_order_id
        self.assertEqual(pedido.state, "sale")
        self.assertEqual(pedido.partner_id, cliente)
        self.assertEqual(pedido.order_line.product_uom_qty, 2)
        self.assertEqual(pedido.order_line.price_unit, 500)
        self.assertEqual(pedido.order_line.qty_delivered, 2)
        self.assertEqual(pedido.invoice_status, "to invoice")
        self.assertFalse(pedido.invoice_ids)
        # con sale_stock instalado, sin entregas pendientes en Inventario
        if "picking_ids" in pedido._fields:
            self.assertFalse(pedido.picking_ids)

        self.assertTrue(mov.envase_movido)
        saldo.invalidate_recordset()
        self.assertEqual(saldo.saldo, 2)
        hoy = mov.fecha_hora.date()
        self.assertEqual(cliente.fecha_ultima_entrega, hoy)
        self.assertEqual(cliente.fecha_ultima_cobranza, hoy)

    def test_r02_fue_migrado_no_se_pisa(self):
        """El FUE/FUC del sistema anterior es un piso: cerrar una hoja
        donde el cliente estuvo ausente no lo borra, y una entrega nueva sí
        lo actualiza."""
        dayroute = self._crear_dayroute(911)
        self._control_playa(dayroute, "salida", [{"product_id": self.sifon.id, "cantidad": 5}])
        cliente = self._crear_cliente("Cliente migrado de WS")
        cliente.write({"fue_legado": date(2025, 3, 1), "fuc_legado": date(2025, 2, 1)})
        self.assertEqual(cliente.fecha_ultima_entrega, date(2025, 3, 1))

        self.env["insa.movimiento.cliente"].create(
            {"hoja_ruta_id": dayroute.id, "partner_id": cliente.id, "resultado": "ausente"}
        )
        self._control_playa(dayroute, "entrada", [{"product_id": self.sifon.id, "cantidad": 5}])
        dayroute.with_user(self.camila)._declarar_efectivo([])
        dayroute.with_user(self.camila)._verificar_efectivo_como(self.controlador, [])
        dayroute.with_user(self.camila).action_finalizar_reparto()

        self.assertEqual(dayroute.state, "cerrada")
        self.assertEqual(cliente.fecha_ultima_entrega, date(2025, 3, 1))
        self.assertEqual(cliente.fecha_ultima_cobranza, date(2025, 2, 1))

    def test_r04_declarar_efectivo_por_denominacion(self):
        """R-04 — el ticket del cajero se carga billete por billete; lo
        declarado se compara con lo cobrado y se puede corregir mientras la
        hoja no esté cerrada."""
        dayroute, cliente, mov = self._reparto_limpio(912, cobro=43000)
        Wizard = self.env["insa.declarar.efectivo.wizard"].with_user(self.camila)
        defaults = Wizard.with_context(default_hoja_ruta_id=dayroute.id).default_get(
            ["hoja_ruta_id", "linea_ids"]
        )
        self.assertEqual(
            [comando[2]["denominacion"] for comando in defaults["linea_ids"]],
            [20000, 10000, 2000, 1000, 500, 200, 100],
        )

        wizard = Wizard.create(
            {
                "hoja_ruta_id": dayroute.id,
                "linea_ids": [
                    (0, 0, {"denominacion": 20000, "cantidad": 2}),
                    (0, 0, {"denominacion": 1000, "cantidad": 2}),
                    (0, 0, {"denominacion": 100, "cantidad": 0}),
                ],
            }
        )
        self.assertEqual(wizard.total_declarado, 42000)
        self.assertEqual(wizard.diferencia, 1000)
        wizard.action_confirmar()

        self.assertEqual(dayroute.state, "consolidando")
        self.assertEqual(dayroute.efectivo_caja_repartidor, 42000)
        self.assertEqual(len(dayroute.declaracion_efectivo_ids), 2)
        self.assertEqual(dayroute.efectivo_declarado_por_id, self.camila)
        self.assertEqual(dayroute.diferencia_efectivo, 1000)
        self.assertTrue(dayroute.tiene_diferencias)

        # corrección: reemplaza la declaración anterior
        dayroute.with_user(self.camila)._declarar_efectivo([(20000, 2), (1000, 3)])
        self.assertEqual(dayroute.efectivo_caja_repartidor, 43000)
        self.assertEqual(len(dayroute.declaracion_efectivo_ids), 2)
        self.assertFalse(dayroute.tiene_diferencias)
        defaults = Wizard.with_context(default_hoja_ruta_id=dayroute.id).default_get(
            ["hoja_ruta_id", "linea_ids"]
        )
        cargado = {c[2]["denominacion"]: c[2]["cantidad"] for c in defaults["linea_ids"]}
        self.assertEqual(cargado[1000], 3)

    def test_r05_control_de_salida_solo_llenos(self):
        """R-05 — "en la salida solo sale con mercadería llena": la vista
        del control oculta vacíos y fallados cuando es de salida (la
        validación en cero sigue en el modelo, CT-N1)."""
        vista = self.env.ref("insa_reparto.insa_control_playa_form")
        arch = etree.fromstring(self.env["insa.control.playa"].get_view(vista.id)["arch"])
        for campo in ("cantidad_vacia", "cantidad_fallada"):
            nodo = arch.xpath("//field[@name='linea_ids']//field[@name='%s']" % campo)[0]
            self.assertEqual(nodo.get("column_invisible"), "parent.tipo == 'salida'")

    def test_r06_correccion_web_venta_extra(self):
        """R-06, el caso real de la reunión: reparto 14 salió con 144,
        entraron 26, teórico 118, registrado 115 — faltaban 3 bidones. Se
        agregan desde la web y el renglón amarillo desaparece."""
        dayroute = self._crear_dayroute(914)
        self._control_playa(
            dayroute, "salida", [{"product_id": self.bidon20_comun.id, "cantidad": 144}]
        )
        cliente = self._crear_cliente("Cliente reparto 14")
        mov = self.env["insa.movimiento.cliente"].create(
            {
                "hoja_ruta_id": dayroute.id,
                "partner_id": cliente.id,
                "linea_articulo_ids": [
                    (0, 0, {"product_id": self.bidon20_comun.id, "cantidad": 115, "precio_unitario": 500})
                ],
            }
        )
        # los envases volvieron completos (118 vacíos por los 118 que se
        # entregaron): la diferencia es solo de registro
        self._control_playa(
            dayroute,
            "entrada",
            [{"product_id": self.bidon20_comun.id, "cantidad": 26, "cantidad_vacia": 118}],
        )
        dayroute.with_user(self.camila)._declarar_efectivo([])
        self.assertEqual(dayroute.liquidacion_articulo_ids.diferencia, 3)
        self.assertTrue(dayroute.tiene_diferencias)

        Linea = self.env["insa.movimiento.cliente.linea.articulo"]
        venta_olvidada = {
            "movimiento_id": mov.id,
            "product_id": self.bidon20_comun.id,
            "cantidad": 3,
            "precio_unitario": 500,
        }
        # consolidando: solo la administrativa corrige
        with self.assertRaises(UserError):
            Linea.with_user(self.repartidor_user).create(venta_olvidada)

        Linea.with_user(self.camila).create(venta_olvidada)
        self.assertEqual(dayroute.liquidacion_articulo_ids.diferencia, 0)
        self.assertFalse(dayroute.tiene_diferencias)
        self.assertIn("Corrección desde la web", str(dayroute.message_ids[0].body))
        self.assertIn("Camila Test", str(dayroute.message_ids[0].body))

    def test_r06_correccion_web_control_de_playa(self):
        """R-06 — la administrativa reabre un control aprobado, corrige el
        conteo y lo vuelve a confirmar ella; queda el antes y el después, y
        la liquidación se recalcula."""
        dayroute, cliente, mov = self._reparto_limpio(915)
        entrada = dayroute.control_entrada_ids
        self.assertEqual(dayroute.liquidacion_articulo_ids.diferencia, 0)

        # un control aprobado no se "confirma como corrección"
        with self.assertRaises(UserError):
            entrada.with_user(self.camila).action_confirmar_correccion()
        # el repartidor no reabre
        with self.assertRaises(UserError):
            entrada.with_user(self.repartidor_user).action_volver_a_borrador()

        entrada.with_user(self.camila).action_volver_a_borrador()
        self.assertTrue(entrada.reabierto_para_correccion)
        entrada.linea_ids.with_user(self.camila).write({"cantidad": 7})
        with self.assertRaises(UserError):
            entrada.with_user(self.repartidor_user).action_confirmar_correccion()
        entrada.with_user(self.camila).action_confirmar_correccion()

        self.assertEqual(entrada.state, "confirmado")
        self.assertEqual(entrada.aprobado_por_id, self.camila)
        self.assertFalse(entrada.reabierto_para_correccion)
        # volvieron 7 llenos en vez de 8: falta un bidón
        self.assertEqual(dayroute.liquidacion_articulo_ids.diferencia, 1)
        mensajes = " ".join(str(m.body) for m in dayroute.message_ids)
        self.assertIn("Conteo antes", mensajes)
        self.assertIn("Conteo después", mensajes)

    def test_r08_hoja_de_ruta_solo_clientes_activos(self):
        """R-08 — la plantilla conserva activos y bajas; la hoja del día
        trae solo los 'Activo' (y las altas nuevas, que no tienen estado de
        Water Service)."""
        dayroute = self._crear_dayroute(916)
        activo = self._crear_cliente("Cliente activo")
        activo.x_insa_estado_ws = "activo"
        baja = self._crear_cliente("Cliente de baja")
        baja.x_insa_estado_ws = "baja"
        moroso = self._crear_cliente("Cliente moroso")
        moroso.x_insa_estado_ws = "moroso"
        alta_nueva = self._crear_cliente("Alta rápida sin estado")

        plantilla = self.env["insa.ruta.plantilla"].create(
            {
                "route_id": dayroute.route_id.id,
                "dia_semana": str(dayroute.date.weekday()),
                "line_ids": [
                    (0, 0, {"partner_id": partner.id, "secuencia": posicion})
                    for posicion, partner in enumerate((activo, baja, moroso, alta_nueva), start=1)
                ],
            }
        )
        resultado = plantilla.action_generar_hoja_ruta(dayroute.date)

        self.assertEqual(resultado["hoja_ruta_id"], dayroute.id)
        self.assertEqual(resultado["paradas_creadas"], 2)
        self.assertEqual(resultado["clientes_inactivos"], 2)
        self.assertEqual(
            dayroute.order_ids.mapped("location_id.partner_id"), activo | alta_nueva
        )
