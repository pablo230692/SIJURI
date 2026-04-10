"""
SiJuri Scraper — Customized for sijuri.irsacorp.com.ar
=======================================================
Based on actual HTML structure observed via DevTools:

LISTING PAGE (Home / Expediente / Listar):
  - URL: /expediente or /expediente/listar
  - Table: .projects-table.dataTable (DataTables plugin)
  - Columns: [expand] | Estudio Jurídico | Actor | Demandado | 
             Objeto de la Demanda | Tipo de Proceso | Acciones
  - Action button: <a class="btn btn-xs btn-default" href="...">
                     <i class="fa fa-eye"></i>
                   </a>
  - Framework: SmartAdmin + Bootstrap + DataTables

DETAIL PAGE (Expediente - {id}):
  - Uses smart-accordion-default panels
  - "Datos del Expediente" section with:
    - Numero (expediente number)
    - Fecha Creación
    - Fecha de última Acción (dd/mm/yyyy)
    - Etapa del Proceso (= estado)
    - Probabilidad de Éxito
    - Última Acción realizada (text description)
    - Plan de Acción
  - "Detalle del Expediente" header with TERMINAR button
  - Tabs for: documents, comments, copies, $, check, calendar
  - "REGISTRAR NUEVA ACCIÓN" button → action history

DASHBOARD:
  - URL: /dashboard
  - Shows: ACTORA count, DEMANDADA count
  - "Últimas Alertas" section
  - User greeting with Area Responsable
"""

import re
import hashlib
import logging
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("sijuri.scraper")


class SijuriScraper:
    """
    Scrapes case data from SiJuri (sijuri.irsacorp.com.ar).
    """

    def __init__(self, base_url: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es-AR,es;q=0.9,en;q=0.5",
            }
        )
        self._logged_in = False

    # ─────────────────────────────────────────────────────────
    #  LOGIN
    # ─────────────────────────────────────────────────────────

    def login(self, username: str, password: str) -> bool:
        """
        Authenticate with SiJuri.
        
        SiJuri uses SmartAdmin framework. Login is typically at /login or /
        The form likely posts to /login with fields like:
          - email / username / user
          - password / pass / clave
          - _token (CSRF if Laravel-based)
        
        ┌─────────────────────────────────────────────────────┐
        │  ADJUST: If the field names differ, update below.   │
        │  To find them: inspect the login form in DevTools.  │
        └─────────────────────────────────────────────────────┘
        """
        log.info(f"Logging in to {self.base_url} as '{username}'...")

        # Step 1: GET the login page to find the form and CSRF token
        login_urls = [
            f"{self.base_url}/login",
            f"{self.base_url}/",
            f"{self.base_url}/auth/login",
            f"{self.base_url}/iniciar-sesion",
        ]

        login_page_url = None
        resp = None
        for url in login_urls:
            try:
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                if resp.status_code == 200 and "password" in resp.text.lower():
                    login_page_url = url
                    log.info(f"Found login page at: {url}")
                    break
            except Exception:
                continue

        if not login_page_url or not resp:
            raise RuntimeError("Could not find login page")

        soup = BeautifulSoup(resp.text, "html.parser")

        # Find the login form
        form = soup.find("form")
        if not form:
            raise RuntimeError("No form found on login page")

        # Extract form action URL
        form_action = form.get("action", login_page_url)
        if form_action and not form_action.startswith("http"):
            form_action = urljoin(self.base_url, form_action)
        log.debug(f"Form action: {form_action}")

        # Extract all hidden fields (CSRF tokens, etc.)
        login_data = {}
        for hidden in form.find_all("input", {"type": "hidden"}):
            name = hidden.get("name")
            value = hidden.get("value", "")
            if name:
                login_data[name] = value
                log.debug(f"Hidden field: {name} = {value[:30]}...")

        # Auto-detect username and password field names
        username_field = None
        password_field = None

        for inp in form.find_all("input"):
            inp_type = (inp.get("type") or "").lower()
            inp_name = inp.get("name", "")
            if inp_type == "password":
                password_field = inp_name
            elif inp_type in ("text", "email") and inp_name and inp_type != "hidden":
                username_field = inp_name

        if not username_field:
            for name in ["email", "username", "user", "login", "usuario"]:
                if form.find("input", {"name": name}):
                    username_field = name
                    break
            if not username_field:
                username_field = "email"

        if not password_field:
            password_field = "password"

        log.info(f"Using fields: user='{username_field}', pass='{password_field}'")
        login_data[username_field] = username
        login_data[password_field] = password

        # Step 2: POST credentials
        resp = self.session.post(
            form_action,
            data=login_data,
            timeout=self.timeout,
            allow_redirects=True,
        )
        resp.raise_for_status()

        # Step 3: Verify login succeeded
        if "dashboard" in resp.url.lower() or "home" in resp.url.lower():
            self._logged_in = True
            log.info(f"Login successful — redirected to {resp.url}")
            return True

        page_text = resp.text.lower()
        if "bienvenido" in page_text or "expediente" in page_text:
            self._logged_in = True
            log.info("Login successful — found authenticated content")
            return True

        error_soup = BeautifulSoup(resp.text, "html.parser")
        error_el = error_soup.find(class_=re.compile(r"error|alert-danger|invalid", re.I))
        error_msg = error_el.get_text(strip=True) if error_el else "Login may have failed"

        if self.session.cookies:
            log.warning(f"Login uncertain but cookies set. Proceeding... ({error_msg})")
            self._logged_in = True
            return True

        raise RuntimeError(f"Login failed: {error_msg}")

    # ─────────────────────────────────────────────────────────
    #  FETCH CASE LIST
    # ─────────────────────────────────────────────────────────

    def fetch_all_cases(self) -> list[dict]:
        """
        Fetch all cases from the expediente listing page.
        Handles both DataTables AJAX and plain HTML rendering.
        """
        if not self._logged_in:
            raise RuntimeError("Must login before fetching cases")

        list_urls = [
            f"{self.base_url}/expediente",
            f"{self.base_url}/expediente/listar",
            f"{self.base_url}/expedientes",
            f"{self.base_url}/expediente/index",
        ]

        resp = None
        list_url = None
        for url in list_urls:
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200 and "listado de expedientes" in resp.text.lower():
                    list_url = url
                    log.info(f"Found listing page at: {url}")
                    break
            except Exception:
                continue

        if not resp or not list_url:
            raise RuntimeError("Could not find expediente listing page")

        soup = BeautifulSoup(resp.text, "html.parser")

        # Check if DataTables uses AJAX
        scripts = soup.find_all("script")
        ajax_url = None
        for script in scripts:
            script_text = script.string or ""
            ajax_match = re.search(
                r'["\']?ajax["\']?\s*:\s*[{]?\s*["\']?url["\']?\s*:\s*["\']([^"\']+)["\']',
                script_text
            )
            if not ajax_match:
                ajax_match = re.search(
                    r'["\']?ajax["\']?\s*:\s*["\']([^"\']+)["\']',
                    script_text
                )
            if ajax_match:
                ajax_url = ajax_match.group(1)
                if not ajax_url.startswith("http"):
                    ajax_url = urljoin(self.base_url, ajax_url)
                log.info(f"Found DataTables AJAX URL: {ajax_url}")
                break

        if ajax_url:
            return self._fetch_cases_via_ajax(ajax_url)
        else:
            return self._fetch_cases_from_html(soup, list_url)

    def _fetch_cases_via_ajax(self, ajax_url: str) -> list[dict]:
        """Fetch cases via DataTables server-side AJAX endpoint."""
        all_cases = []
        start = 0
        length = 100

        while True:
            params = {
                "draw": 1,
                "start": start,
                "length": length,
                "search[value]": "",
                "order[0][column]": 0,
                "order[0][dir]": "asc",
            }

            log.info(f"Fetching via AJAX: start={start}, length={length}")
            resp = self.session.get(ajax_url, params=params, timeout=self.timeout)
            resp.raise_for_status()

            try:
                data = resp.json()
            except Exception:
                log.warning("AJAX response is not JSON, falling back to HTML")
                soup = BeautifulSoup(resp.text, "html.parser")
                return self._fetch_cases_from_html(soup, ajax_url)

            records = data.get("data") or data.get("aaData") or []
            total = data.get("recordsTotal") or data.get("iTotalRecords") or 0

            for record in records:
                case = self._parse_ajax_record(record)
                if case:
                    all_cases.append(case)

            log.info(f"Got {len(records)} records (total: {total})")

            start += length
            if start >= int(total) or not records:
                break
            time.sleep(0.5)

        self._enrich_cases_with_details(all_cases)
        return all_cases

    def _parse_ajax_record(self, record) -> Optional[dict]:
        """Parse a single record from DataTables AJAX response."""
        try:
            if isinstance(record, list):
                offset = 0
                if len(record) > 6 and "<" in str(record[0]):
                    offset = 1

                estudio = self._strip_html(record[0 + offset])
                actor = self._strip_html(record[1 + offset])
                demandado = self._strip_html(record[2 + offset])
                objeto = self._strip_html(record[3 + offset])
                tipo = self._strip_html(record[4 + offset])
                acciones_html = str(record[5 + offset]) if (5 + offset) < len(record) else ""

            elif isinstance(record, dict):
                estudio = self._strip_html(record.get("estudio", record.get("estudio_juridico", "")))
                actor = self._strip_html(record.get("actor", ""))
                demandado = self._strip_html(record.get("demandado", ""))
                objeto = self._strip_html(record.get("objeto", record.get("objeto_demanda", "")))
                tipo = self._strip_html(record.get("tipo", record.get("tipo_proceso", "")))
                acciones_html = str(record.get("acciones", record.get("action", "")))
            else:
                return None

            detail_url, case_id = self._extract_link_and_id(acciones_html)
            if not case_id:
                case_id = hashlib.md5(f"{actor}{demandado}{objeto}".encode()).hexdigest()[:8]

            nombre = f"{actor} c/ {demandado} s/ {objeto}"

            return {
                "id": case_id,
                "expediente": "",
                "nombre": nombre,
                "actor": actor,
                "demandado": demandado,
                "objeto_demanda": objeto,
                "tipo_proceso": tipo,
                "estudio": estudio,
                "estado": "",
                "juzgado": "",
                "fuero": "",
                "ultimoMov": "",
                "movimientos": [],
                "probabilidad_exito": "",
                "plan_accion": "",
                "_detail_url": detail_url,
            }
        except Exception as e:
            log.warning(f"Failed to parse AJAX record: {e}")
            return None

    def _fetch_cases_from_html(self, soup: BeautifulSoup, page_url: str) -> list[dict]:
        """Parse cases from the HTML table (.projects-table.dataTable)."""
        all_cases = []

        table = (
            soup.find("table", class_=re.compile(r"projects-table|dataTable"))
            or soup.find("table", {"id": re.compile(r"expediente|datatable", re.I)})
            or soup.find("table", class_="table")
        )
        if not table:
            log.error("Could not find expediente table in HTML")
            return []

        tbody = table.find("tbody")
        if not tbody:
            log.error("No tbody in table")
            return []

        rows = tbody.find_all("tr")
        log.info(f"Found {len(rows)} rows in table")

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 6:
                continue
            try:
                # Detect if first column is an expand button (+)
                offset = 0
                first_col = cols[0]
                if first_col.find("span") or first_col.find("i") or not first_col.get_text(strip=True):
                    offset = 1

                estudio = cols[0 + offset].get_text(strip=True)
                actor = cols[1 + offset].get_text(strip=True)
                demandado = cols[2 + offset].get_text(strip=True)
                objeto = cols[3 + offset].get_text(strip=True)
                tipo = cols[4 + offset].get_text(strip=True)

                actions_col = cols[5 + offset] if (5 + offset) < len(cols) else cols[-1]
                detail_link = actions_col.find("a", href=True)
                detail_url = ""
                case_id = ""
                if detail_link:
                    detail_url = detail_link["href"]
                    if not detail_url.startswith("http"):
                        detail_url = urljoin(self.base_url, detail_url)
                    id_match = re.search(r'/(\d+)', detail_url)
                    if id_match:
                        case_id = id_match.group(1)

                if not case_id:
                    case_id = hashlib.md5(f"{actor}{demandado}{objeto}".encode()).hexdigest()[:8]

                nombre = f"{actor} c/ {demandado} s/ {objeto}"

                all_cases.append({
                    "id": case_id,
                    "expediente": "",
                    "nombre": nombre,
                    "actor": actor,
                    "demandado": demandado,
                    "objeto_demanda": objeto,
                    "tipo_proceso": tipo,
                    "estudio": estudio,
                    "estado": "",
                    "juzgado": "",
                    "fuero": "",
                    "ultimoMov": "",
                    "movimientos": [],
                    "probabilidad_exito": "",
                    "plan_accion": "",
                    "_detail_url": detail_url,
                })
            except Exception as e:
                log.warning(f"Failed to parse row: {e}")
                continue

        self._enrich_cases_with_details(all_cases)
        return all_cases

    # ─────────────────────────────────────────────────────────
    #  FETCH CASE DETAILS
    # ─────────────────────────────────────────────────────────

    def _enrich_cases_with_details(self, cases: list[dict]):
        """Fetch detail page for each case to get status, dates, actions."""
        for i, case in enumerate(cases):
            detail_url = case.get("_detail_url", "")
            if not detail_url:
                continue
            log.info(f"Fetching details ({i+1}/{len(cases)}): {detail_url}")
            try:
                detail = self._fetch_case_detail(detail_url)
                case.update(detail)
            except Exception as e:
                log.warning(f"Failed to fetch detail for case {case['id']}: {e}")
            time.sleep(0.3)

    def _fetch_case_detail(self, detail_url: str) -> dict:
        """
        Parse the detail page of a single expediente.
        
        Structure:
        - Title: "Expediente - 35 (Actor c/Demandado s/Objeto)"
        - Badges: ESTADO DEL EXPEDIENTE | FECHA DE ULTIMA ACCION JUDICIAL
        - Accordion: "Datos del Expediente"
          Fields: Numero, Fecha Creación, Fecha última Acción,
                  Etapa del Proceso, Probabilidad de Éxito,
                  Última Acción realizada, Plan de Acción
        """
        resp = self.session.get(detail_url, timeout=self.timeout)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        result = {}

        # ── Title: "Expediente - 35 (...)" ──
        title_el = soup.find("h1") or soup.find("h2")
        if title_el:
            title_text = title_el.get_text(strip=True)
            id_match = re.search(r'Expediente\s*-\s*(\d+)', title_text)
            if id_match:
                result["expediente"] = f"EXP-{id_match.group(1)}"

        # ── ESTADO DEL EXPEDIENTE badge ──
        estado_label = soup.find(string=re.compile(r"ESTADO DEL EXPEDIENTE", re.I))
        if estado_label:
            parent = estado_label.parent
            if parent:
                badge = parent.find_next(class_=re.compile(r"label|badge|btn|alert"))
                if not badge:
                    badge = parent.find_next_sibling()
                if badge:
                    raw_estado = badge.get_text(strip=True)
                    result["estado"] = self._normalize_status(raw_estado)
                    result["etapa_proceso"] = raw_estado

        # ── FECHA DE ULTIMA ACCION JUDICIAL badge ──
        fecha_label = soup.find(string=re.compile(r"FECHA DE ULTIMA ACCION", re.I))
        if fecha_label:
            parent = fecha_label.parent
            if parent:
                badge = parent.find_next(class_=re.compile(r"label|badge|btn|alert"))
                if not badge:
                    badge = parent.find_next_sibling()
                if badge:
                    result["ultimoMov"] = self._parse_date(badge.get_text(strip=True))

        # ── "Datos del Expediente" field-value pairs ──
        field_mappings = {
            "numero": "expediente_numero",
            "fecha creación": "fecha_creacion",
            "fecha de última acción": "fecha_ultima_accion",
            "etapa del proceso": "etapa_proceso",
            "probabilidad de éxito": "probabilidad_exito",
            "última acción realizada": "ultima_accion",
            "plan de acción": "plan_accion",
        }

        for label_text, field_name in field_mappings.items():
            label_el = soup.find(string=re.compile(re.escape(label_text), re.I))
            if not label_el:
                continue
            parent = label_el.parent
            if not parent:
                continue

            value_el = parent.find_next(["input", "select", "textarea"])
            if value_el:
                if value_el.name == "select":
                    selected = value_el.find("option", selected=True)
                    value = selected.get_text(strip=True) if selected else ""
                elif value_el.name == "textarea":
                    value = value_el.get_text(strip=True)
                else:
                    value = value_el.get("value", "") or value_el.get_text(strip=True)
            else:
                next_el = parent.find_next_sibling()
                value = next_el.get_text(strip=True) if next_el else ""

            if value and field_name not in result:
                result[field_name] = value

        # Fill expediente from detail number
        if "expediente_numero" in result and not result.get("expediente"):
            result["expediente"] = result["expediente_numero"]

        # Parse dates
        for date_field in ["fecha_creacion", "fecha_ultima_accion"]:
            if date_field in result:
                result[date_field] = self._parse_date(result[date_field])

        if not result.get("ultimoMov") and result.get("fecha_ultima_accion"):
            result["ultimoMov"] = result["fecha_ultima_accion"]

        if not result.get("estado") and result.get("etapa_proceso"):
            result["estado"] = self._normalize_status(result["etapa_proceso"])

        # ── Extract action history / movements ──
        result["movimientos"] = self._extract_movements(soup)

        return result

    def _extract_movements(self, soup: BeautifulSoup) -> list[dict]:
        """Extract movement/action history from the detail page."""
        movements = []

        # Strategy 1: Look for an actions table
        tables = soup.find_all("table")
        for table in tables:
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if any(h in str(headers) for h in ["fecha", "acción", "accion", "descripción"]):
                rows = table.find("tbody")
                if rows:
                    for row in rows.find_all("tr"):
                        cols = row.find_all("td")
                        if len(cols) >= 2:
                            mov = self._parse_movement_row(cols, headers)
                            if mov:
                                movements.append(mov)

        # Strategy 2: Timeline or accordion items
        if not movements:
            panels = soup.find_all(class_=re.compile(r"panel|timeline-item|activity|accion", re.I))
            for panel in panels:
                date_el = panel.find(string=re.compile(r'\d{2}/\d{2}/\d{4}'))
                text_el = panel.find(class_=re.compile(r"desc|title|text|content", re.I))
                if date_el:
                    movements.append({
                        "fecha": self._parse_date(date_el.strip()),
                        "texto": text_el.get_text(strip=True) if text_el else "",
                        "detalle": "",
                    })

        # Strategy 3: Build a movement from "Última Acción realizada"
        if not movements:
            ultima_accion = soup.find(string=re.compile(r"Última Acción realizada", re.I))
            if ultima_accion:
                parent = ultima_accion.parent
                if parent:
                    value_el = parent.find_next(["textarea", "div", "p", "input"])
                    if value_el:
                        value = value_el.get("value", "") or value_el.get_text(strip=True)
                        if value and value != "0":
                            fecha_el = soup.find(string=re.compile(r"Fecha de última Acción", re.I))
                            fecha = ""
                            if fecha_el and fecha_el.parent:
                                fecha_val = fecha_el.parent.find_next(["input", "div"])
                                if fecha_val:
                                    fecha = self._parse_date(
                                        fecha_val.get("value", "") or fecha_val.get_text(strip=True)
                                    )
                            movements.append({
                                "fecha": fecha,
                                "texto": value[:300],
                                "detalle": "Última acción registrada",
                            })

        movements.sort(key=lambda m: m.get("fecha", ""), reverse=True)
        return movements

    def _parse_movement_row(self, cols: list, headers: list) -> Optional[dict]:
        """Parse a table row into a movement dict."""
        try:
            fecha, texto, detalle = "", "", ""
            for i, col in enumerate(cols):
                header = headers[i] if i < len(headers) else ""
                value = col.get_text(strip=True)
                if "fecha" in header:
                    fecha = self._parse_date(value)
                elif any(k in header for k in ["acción", "accion", "descripción", "descripcion", "tipo"]):
                    texto = value
                elif any(k in header for k in ["detalle", "observ", "nota", "comentario"]):
                    detalle = value
                elif not texto:
                    texto = value
            if texto or fecha:
                return {"fecha": fecha, "texto": texto, "detalle": detalle}
        except Exception:
            pass
        return None

    # ─────────────────────────────────────────────────────────
    #  UTILITY METHODS
    # ─────────────────────────────────────────────────────────

    def _extract_link_and_id(self, html: str) -> tuple[str, str]:
        """Extract href and case ID from action button HTML."""
        detail_url = ""
        case_id = ""
        href_match = re.search(r'href=["\']([^"\']*)["\']', html)
        if href_match:
            detail_url = href_match.group(1)
            if not detail_url.startswith("http"):
                detail_url = urljoin(self.base_url, detail_url)
            id_match = re.search(r'/(\d+)', detail_url)
            if id_match:
                case_id = id_match.group(1)
        return detail_url, case_id

    @staticmethod
    def _strip_html(text: str) -> str:
        """Remove HTML tags from a string."""
        if "<" in str(text):
            return BeautifulSoup(str(text), "html.parser").get_text(strip=True)
        return str(text).strip()

    @staticmethod
    def _normalize_status(raw: str) -> str:
        """Map raw status/etapa text to standard values for the PWA."""
        raw = raw.lower().strip()
        mappings = {
            "demanda iniciada": "activo",
            "en trámite": "activo",
            "en tramite": "activo",
            "abierto": "activo",
            "en curso": "activo",
            "activo": "activo",
            "iniciado": "activo",
            "contestación": "activo",
            "contestacion": "activo",
            "prueba": "activo",
            "apertura a prueba": "activo",
            "alegatos": "activo",
            "sentencia": "activo",
            "ejecución": "activo",
            "ejecucion": "activo",
            "en espera": "en espera",
            "pendiente": "en espera",
            "suspendido": "en espera",
            "paralizado": "en espera",
            "mediación": "en espera",
            "mediacion": "en espera",
            "urgente": "urgente",
            "prioridad": "urgente",
            "vencido": "urgente",
            "apelación": "urgente",
            "apelacion": "urgente",
            "cerrado": "cerrado",
            "archivado": "cerrado",
            "finalizado": "cerrado",
            "terminado": "cerrado",
            "concluido": "cerrado",
            "desistido": "cerrado",
            "acuerdo": "cerrado",
        }
        for key, value in mappings.items():
            if key in raw:
                return value
        return "activo"

    @staticmethod
    def _parse_date(date_str: str) -> str:
        """Parse date string to YYYY-MM-DD format."""
        if not date_str:
            return ""
        date_str = date_str.strip()
        match = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})', date_str)
        if match:
            d, m, y = match.groups()
            if len(y) == 2:
                y = "20" + y if int(y) < 50 else "19" + y
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"

        for pattern in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"]:
            try:
                return datetime.strptime(date_str, pattern).strftime("%Y-%m-%d")
            except ValueError:
                continue
        return date_str
