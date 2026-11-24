"""
EcoScan v3.0 — Análisis de Suelo y Plantas con IA + Base de Datos de Referencia + OpenLandMap-soildb / SoilGrids
Ejecutar: python server.py
"""

import json, sqlite3, hashlib, hmac, re, secrets, os, sys, base64, threading, webbrowser, time
import datetime, importlib.util, math, unicodedata
import atexit, faulthandler, shutil, subprocess, tempfile
from collections import defaultdict, deque
from datetime import timedelta
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutureTimeout
import requests as _req
from flask import Flask, request, jsonify, send_from_directory, session, make_response, abort, g
from groq import Groq
from werkzeug.security import generate_password_hash, check_password_hash

# Si el proceso cae por un error NATIVO (GDAL, SSL, un controlador…), Python no imprime nada y la ventana parece
# "cerrarse sola". Con esto, antes de morir deja en la consola el origen del fallo (archivo y línea de cada hilo).
try:
    faulthandler.enable()
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Lee un archivo .env sencillo (CLAVE=valor) sin pisar variables que ya estén definidas en el sistema."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"  ⚠️  No se pudo leer {os.path.basename(path)}: {e}")


_load_dotenv(os.path.join(BASE_DIR, ".env"))

# La llave de Groq ya NO va dentro del código: se lee de la variable de entorno GROQ_API_KEY
# o del archivo .env que crea iniciar_ecoscan.bat (nunca lo subas a GitHub).
GROQ_API_KEY    = (os.environ.get("GROQ_API_KEY") or "").strip()
GROQ_CONFIGURED = bool(GROQ_API_KEY)
DB_PATH         = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "ecoscan.db"))
PORT            = int(os.environ.get("PORT", 5050))
HOST            = os.environ.get("HOST", "127.0.0.1")      # 127.0.0.1 = solo tu equipo; 0.0.0.0 = también tu red local
IS_PROD         = bool(os.environ.get("RENDER")) or os.environ.get("ECOSCAN_ENV", "").lower() == "production"
MAX_UPLOAD_MB   = int(os.environ.get("MAX_UPLOAD_MB", 12))
AI_RPM          = int(os.environ.get("AI_RPM", 60))          # peticiones de IA por usuario y por minuto
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]


def _resolve_secret_key():
    """SECRET_KEY del entorno; si no existe, se genera UNA vez y se guarda en .secret_key (así las sesiones sobreviven a reinicios)."""
    env = os.environ.get("SECRET_KEY", "")
    if len(env) >= 16:
        return env
    path = os.path.join(BASE_DIR, ".secret_key")
    try:
        with open(path, "r", encoding="utf-8") as f:
            val = f.read().strip()
        if len(val) >= 32:
            return val
    except OSError:
        pass
    val = secrets.token_hex(32)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(val)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        print("  ⚠️  No se pudo guardar .secret_key; las sesiones se cerrarán al reiniciar. Define SECRET_KEY en el entorno.")
    return val


SECRET_KEY = _resolve_secret_key()

client = Groq(api_key=GROQ_API_KEY or "sin-configurar")
app    = Flask(__name__, static_folder=None)     # sin carpeta estática: solo se sirven los archivos permitidos más abajo
app.secret_key = SECRET_KEY
app.config.update(
    SESSION_COOKIE_NAME="ecoscan_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="None" if ALLOWED_ORIGINS else "Lax",
    SESSION_COOKIE_SECURE=bool(IS_PROD or ALLOWED_ORIGINS or os.environ.get("COOKIE_SECURE") == "1"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
)
if IS_PROD or os.environ.get("TRUST_PROXY") == "1":
    # Detrás del proxy de Render: así request.remote_addr / request.host / https son los reales
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if ALLOWED_ORIGINS:
    # El front-end se sirve desde este mismo servidor, así que CORS NO hace falta; solo se activa si lo pides explícitamente.
    from flask_cors import CORS
    CORS(app, supports_credentials=True, origins=ALLOWED_ORIGINS)


SOIL_DB = [
    {
        "id": "vertisol_dark",
        "name": "Vertisol (Negro/Gris oscuro)",
        "fao_class": "Vertisol", "usda_class": "Vertisols",
        "type_keys": ["arcilloso", "franco_arcilloso"],
        "color_keywords": ["negro", "gris oscuro", "gris", "pardo oscuro", "dark"],
        "ph_range": [6.0, 8.5], "ph_typical": 7.2,
        "clay_pct": [40, 70], "sand_pct": [5, 25],
        "compaction_typical": "alta",
        "drainage_typical": "pobre",
        "organic_matter_level": "medio",
        "salinity_risk": "media",
        "regions": ["Bajío mexicano", "Chiapas", "Veracruz", "Valle de México", "llanuras aluviales tropicales"],
        "visual_markers": ["grietas profundas al secar", "superficie resbaladiza húmedo", "terrones duros", "color uniforme oscuro"],
        "typical_crops": ["maíz", "trigo", "sorgo", "caña de azúcar", "algodón", "girasol"],
        "native_plants": ["huizache (Acacia farnesiana)", "tepozán (Buddleja cordata)", "palo verde (Cercidium praecox)"],
        "issues": ["compactación severa", "mal drenaje en lluvias", "alta plasticidad", "grietas en estiaje"],
        "amendments": ["subsolado periódico", "incorporar yeso agrícola", "cultivos de cobertura con raíz profunda", "cal agrícola si pH > 8"],
        "fertility": "alta",
        "score_boost": ["grietas", "arcilla", "vertisol", "expansivo", "negro profundo"]
    },
    {
        "id": "andosol_dark",
        "name": "Andosol (Suelo volcánico oscuro)",
        "fao_class": "Andosol", "usda_class": "Andisols",
        "type_keys": ["franco", "franco_arcilloso", "organico"],
        "color_keywords": ["negro", "marrón muy oscuro", "café oscuro", "dark brown", "negro esponjoso"],
        "ph_range": [5.0, 7.0], "ph_typical": 5.8,
        "clay_pct": [15, 35], "sand_pct": [20, 40],
        "compaction_typical": "baja",
        "drainage_typical": "bueno",
        "organic_matter_level": "alto",
        "salinity_risk": "baja",
        "regions": ["faldas de volcanes", "Sierra Nevada", "Popocatépetl", "Pico de Orizaba", "Eje Neovolcánico México", "Andes (Sudamérica)", "Japón", "Indonesia"],
        "visual_markers": ["muy esponjoso y ligero", "retiene agua como esponja", "color negro uniforme profundo", "ceniza volcánica visible"],
        "typical_crops": ["papa", "maíz de altura", "aguacate", "flores de corte", "hortalizas", "durazno"],
        "native_plants": ["oyamel (Abies religiosa)", "pino montezuma (Pinus montezumae)", "zacatón (Muhlenbergia macroura)", "aile (Alnus acuminata)"],
        "issues": ["alta fijación de fósforo", "acidez moderada", "riesgo de erosión hídrica en pendientes"],
        "amendments": ["fosfato bicálcico", "encalado leve", "materia orgánica para CIC", "terrazas en laderas"],
        "fertility": "alta",
        "score_boost": ["volcánico", "andosol", "esponjoso", "ligero", "ceniza"]
    },
    {
        "id": "feozem",
        "name": "Feozem (Tierra parda fértil)",
        "fao_class": "Phaeozem", "usda_class": "Mollisols",
        "type_keys": ["franco", "franco_arcilloso", "franco_arenoso"],
        "color_keywords": ["pardo oscuro", "marrón oscuro", "café rojizo oscuro", "dark brown"],
        "ph_range": [6.0, 7.5], "ph_typical": 6.8,
        "clay_pct": [18, 35], "sand_pct": [25, 45],
        "compaction_typical": "media",
        "drainage_typical": "moderado",
        "organic_matter_level": "alto",
        "salinity_risk": "baja",
        "regions": ["altiplano central México", "Jalisco", "Michoacán", "Guanajuato", "zonas templadas sub-húmedas"],
        "visual_markers": ["horizonte superficial oscuro y fértil", "buena estructura granular", "color oscuro uniforme arriba claro abajo"],
        "typical_crops": ["maíz", "frijol", "sorgo", "trigo", "agave", "nopal"],
        "native_plants": ["encino (Quercus spp.)", "fresno (Fraxinus uhdei)", "pirúl (Schinus molle)", "trueno (Ligustrum japonicum)"],
        "issues": ["erosión si se deja descubierto", "compactación por maquinaria"],
        "amendments": ["mantenimiento de cobertura vegetal", "abonos verdes", "rotación de cultivos"],
        "fertility": "muy alta",
        "score_boost": ["feozem", "fértil", "mollisol", "horizonte oscuro", "pradera"]
    },
    {
        "id": "luvisol_red",
        "name": "Luvisol / Acrisol (Rojizo con arcilla)",
        "fao_class": "Luvisol/Acrisol", "usda_class": "Alfisols/Ultisols",
        "type_keys": ["arcilloso", "franco_arcilloso"],
        "color_keywords": ["rojizo", "rojo", "rojo pálido", "rojo amarillento", "ocre", "terracota"],
        "ph_range": [4.5, 6.5], "ph_typical": 5.5,
        "clay_pct": [25, 55], "sand_pct": [15, 35],
        "compaction_typical": "media",
        "drainage_typical": "moderado",
        "organic_matter_level": "bajo",
        "salinity_risk": "baja",
        "regions": ["trópico húmedo México", "Tabasco", "Chiapas", "Veracruz", "Yucatán", "Brasil", "África tropical"],
        "visual_markers": ["color rojizo saturado", "endurecimiento al secar", "horizonte B arcilloso", "superficie a veces encostrante"],
        "typical_crops": ["cacao", "café", "plátano", "piña", "yuca", "palma africana"],
        "native_plants": ["ceiba (Ceiba pentandra)", "caoba (Swietenia macrophylla)", "zapote (Manilkara zapota)", "palmera coyol (Acrocomia aculeata)"],
        "issues": ["acidez", "alta saturación de aluminio tóxico", "baja fertilidad", "lixiviación de nutrientes"],
        "amendments": ["encalado con dolomita", "fósforo soluble", "materia orgánica en superficie", "plantas fijadoras de N"],
        "fertility": "baja",
        "score_boost": ["rojo", "rojizo", "acrisol", "luvisol", "arcilla roja", "oxisol"]
    },
    {
        "id": "cambisol_brown",
        "name": "Cambisol (Pardo forestal joven)",
        "fao_class": "Cambisol", "usda_class": "Inceptisols",
        "type_keys": ["franco", "franco_arcilloso"],
        "color_keywords": ["pardo", "marrón", "café", "café claro", "ocre pálido", "brown"],
        "ph_range": [5.5, 7.5], "ph_typical": 6.5,
        "clay_pct": [18, 35], "sand_pct": [20, 40],
        "compaction_typical": "media",
        "drainage_typical": "moderado",
        "organic_matter_level": "medio",
        "salinity_risk": "baja",
        "regions": ["zonas boscosas templadas", "Sierra Madre Occidental", "Sierra Madre Oriental", "Europa central", "Asia templada"],
        "visual_markers": ["color pardo uniforme", "estructura moderada", "raíces abundantes", "hojarasca visible"],
        "typical_crops": ["papa", "hortalizas", "manzana", "pera", "durazno", "maíz de temporal"],
        "native_plants": ["pino (Pinus spp.)", "encino (Quercus spp.)", "madroño (Arbutus xalapensis)", "cedrillo (Cupressus lindleyi)"],
        "issues": ["variable fertilidad", "susceptible a erosión en pendiente"],
        "amendments": ["compost", "encalado moderado si < pH 5.5", "cobertura vegetal"],
        "fertility": "media",
        "score_boost": ["cambisol", "forestal", "bosque", "pino", "inceptisol"]
    },
    {
        "id": "regosol_sandy",
        "name": "Regosol / Arenosol (Arenoso claro)",
        "fao_class": "Regosol/Arenosol", "usda_class": "Entisols",
        "type_keys": ["arenoso", "franco_arenoso"],
        "color_keywords": ["beige", "crema", "amarillo claro", "gris claro", "blanco amarillento", "arena", "pálido"],
        "ph_range": [6.0, 8.5], "ph_typical": 7.5,
        "clay_pct": [2, 15], "sand_pct": [60, 90],
        "compaction_typical": "baja",
        "drainage_typical": "bueno",
        "organic_matter_level": "bajo",
        "salinity_risk": "variable",
        "regions": ["zonas áridas norte México", "Chihuahua", "Sonora", "Baja California", "desiertos tropicales", "costas arenosas"],
        "visual_markers": ["grano claramente visible", "suelto y sin cohesión", "muy claro", "escurre entre dedos fácilmente"],
        "typical_crops": ["melón", "sandía", "espárrago", "uva con riego", "dátil", "nopal forrajero"],
        "native_plants": ["mezquite (Prosopis juliflora)", "gobernadora (Larrea tridentata)", "nopal cardón (Pachycereus pringlei)", "palo fierro (Olneya tesota)"],
        "issues": ["muy baja retención de agua", "baja CEC", "lixiviación rápida de nutrientes", "erosión eólica"],
        "amendments": ["incorporar arcilla o bentonita", "materia orgánica abundante", "riego por goteo", "mulching grueso"],
        "fertility": "muy baja",
        "score_boost": ["arena", "arenoso", "suelto", "claro", "seco", "desierto"]
    },
    {
        "id": "fluvisol",
        "name": "Fluvisol (Suelo aluvial de ribera)",
        "fao_class": "Fluvisol", "usda_class": "Entisols (Fluvents)",
        "type_keys": ["franco", "franco_arenoso", "limoso", "franco_arcilloso"],
        "color_keywords": ["marrón grisáceo", "gris parduzco", "pardo claro", "estratificado", "mezclado", "gris"],
        "ph_range": [5.5, 7.5], "ph_typical": 6.8,
        "clay_pct": [10, 35], "sand_pct": [20, 60],
        "compaction_typical": "baja",
        "drainage_typical": "variable",
        "organic_matter_level": "medio",
        "salinity_risk": "variable",
        "regions": ["márgenes de ríos", "valles aluviales", "deltas", "planicies de inundación", "Valle del Yaqui", "Valle del Mezquital"],
        "visual_markers": ["capas horizontales visibles (estratificación)", "sedimentos recientes", "fragmentos orgánicos", "aspecto heterogéneo"],
        "typical_crops": ["arroz", "maíz", "hortalizas de ciclo corto", "alfalfa", "caña de azúcar"],
        "native_plants": ["sauce llorón (Salix babylonica)", "fresno (Fraxinus uhdei)", "ahuejote (Salix bonplandiana)", "sauce sauce (Populus nigra)"],
        "issues": ["riesgo de inundación", "variabilidad textural", "posible contaminación aguas arriba"],
        "amendments": ["drenaje si es necesario", "análisis de metales pesados en zonas urbanas", "incorporar materia orgánica"],
        "fertility": "media a alta",
        "score_boost": ["aluvial", "río", "sedimento", "fluvisol", "inundación", "estratos"]
    },
    {
        "id": "leptosol",
        "name": "Leptosol (Suelo delgado y pedregoso)",
        "fao_class": "Leptosol", "usda_class": "Entisols (Lithic)",
        "type_keys": ["franco", "arenoso"],
        "color_keywords": ["gris", "ocre", "pardo claro", "rojizo claro", "pedregoso", "fragmentos rocosos"],
        "ph_range": [6.0, 8.5], "ph_typical": 7.2,
        "clay_pct": [5, 25], "sand_pct": [30, 65],
        "compaction_typical": "alta",
        "drainage_typical": "bueno",
        "organic_matter_level": "bajo",
        "salinity_risk": "baja",
        "regions": ["Sierra Madre Occidental", "Sierra Madre Oriental", "áreas cársticas Yucatán", "zonas montañosas escarpadas"],
        "visual_markers": ["mucha piedra y grava", "poca profundidad (< 25 cm)", "roca madre aflorante", "vegetación escasa"],
        "typical_crops": ["nopales", "agaves", "hierbas aromáticas", "viticultura extensiva", "ganadería extensiva"],
        "native_plants": ["lechuguilla (Agave lechuguilla)", "candelilla (Euphorbia antisyphilitica)", "jatropha (Jatropha dioica)", "garambullo (Myrtillocactus geometrizans)"],
        "issues": ["poca profundidad radicular", "baja capacidad de retención de agua", "erosión severa"],
        "amendments": ["terrazas de muro vivo", "biofertilizantes", "especies de raíz adaptada", "materia orgánica en hoyos"],
        "fertility": "muy baja",
        "score_boost": ["pedregoso", "leptosol", "roca", "delgado", "sierra", "piedra"]
    },
    {
        "id": "solonchak",
        "name": "Solonchak (Suelo salino)",
        "fao_class": "Solonchak", "usda_class": "Aridisols (Salids)",
        "type_keys": ["arcilloso", "franco_arcilloso", "limoso"],
        "color_keywords": ["blanco", "grisáceo blancuzco", "costra blanca", "eflorescencias", "manchas blancas", "costras"],
        "ph_range": [7.5, 9.5], "ph_typical": 8.5,
        "clay_pct": [20, 45], "sand_pct": [10, 35],
        "compaction_typical": "alta",
        "drainage_typical": "pobre",
        "organic_matter_level": "bajo",
        "salinity_risk": "muy alta",
        "regions": ["costa Pacífico Norte México", "Sinaloa litoral", "Yucatán costa", "Sonora costas", "zonas áridas irrigadas", "Mesopotamia", "Australia árida"],
        "visual_markers": ["manchas o costras blancas en superficie", "eflorescencias salinas", "vegetación halófita escasa", "aspecto como escarcha o nieve"],
        "typical_crops": ["espárrago tolerante sal", "remolacha azucarera", "algodón resistente", "quinoa (moderada)"],
        "native_plants": ["mangle negro (Avicennia germinans)", "ciénega (Distichlis spicata)", "chamizo (Atriplex canescens)", "salicornia (Salicornia bigelovii)"],
        "issues": ["toxicidad iónica", "desequilibrio nutricional", "impermeabilización de poros", "fitotoxicidad generalizada"],
        "amendments": ["lavado intensivo con agua dulce", "yeso agrícola 8-12 ton/ha", "drenaje subsuperficial obligatorio", "azufre elemental si pH > 8.5"],
        "fertility": "muy baja (por toxicidad)",
        "score_boost": ["sal", "salino", "blanco", "costra", "eflorescencia", "solonchak"]
    },
    {
        "id": "histosol",
        "name": "Histosol / Suelo Orgánico (Turba/Humus)",
        "fao_class": "Histosol", "usda_class": "Histosols",
        "type_keys": ["organico"],
        "color_keywords": ["negro profundo", "muy oscuro", "negro esponjoso", "materia orgánica visible", "turba"],
        "ph_range": [3.5, 6.5], "ph_typical": 4.5,
        "clay_pct": [0, 10], "sand_pct": [0, 15],
        "compaction_typical": "baja",
        "drainage_typical": "pobre",
        "organic_matter_level": "muy alto",
        "salinity_risk": "baja",
        "regions": ["chinampas CDMX-Xochimilco", "humedales Tabasco", "pantanos Campeche", "selvas inundables Chiapas", "turberas europeas", "pantanos Ecuador/Colombia"],
        "visual_markers": ["esponjoso y liviano", "negro profundo homogéneo", "restos vegetales parcialmente descompuestos", "olor a tierra fértil"],
        "typical_crops": ["hortalizas en chinampa", "arroz", "cítricos con drenaje", "flores de ornato"],
        "native_plants": ["ahuejote (Salix bonplandiana)", "chinamite (Senecio praecox)", "tule (Typha latifolia)", "carrizal (Phragmites australis)"],
        "issues": ["acidez alta", "subsidencia al drenar", "deficiencia de micronutrientes", "fitotoxicidad por aluminio"],
        "amendments": ["cal calcítica para subir pH", "micronutrientes (Fe, Mn, Zn)", "no drenar completamente", "mantener humedad"],
        "fertility": "alta (pero acidez limita cultivos)",
        "score_boost": ["orgánico", "turba", "humus", "histosol", "chinampa", "esponjoso negro"]
    },
    {
        "id": "oxisol_ferralsol",
        "name": "Oxisol / Ferralsol (Rojo tropical profundo)",
        "fao_class": "Ferralsol", "usda_class": "Oxisols",
        "type_keys": ["arcilloso", "franco_arcilloso"],
        "color_keywords": ["rojo intenso", "rojo ladrillo", "óxido", "anaranjado rojizo", "rojo oscuro", "ferroso"],
        "ph_range": [4.0, 6.0], "ph_typical": 4.8,
        "clay_pct": [40, 80], "sand_pct": [5, 25],
        "compaction_typical": "media",
        "drainage_typical": "bueno",
        "organic_matter_level": "bajo",
        "salinity_risk": "baja",
        "regions": ["trópico húmedo profundo", "Tabasco interior", "Brasil (cerrado/Amazonía)", "África central", "Indonesia", "zonas con temperatura alta todo el año"],
        "visual_markers": ["rojo intenso y uniforme en todo el perfil", "textura firme pero friable en seco", "sin estratificación visible", "arcilla con estructura granular fina"],
        "typical_crops": ["soya", "maíz tropical", "caña de azúcar", "café", "eucalipto (forestería)"],
        "native_plants": ["cedrón (Simaba cedron)", "zapote colorado (Pouteria sapota)", "chicozapote (Manilkara zapota)"],
        "issues": ["alta fijación de P (hasta 90%)", "toxicidad de Al y Mn", "muy baja CEC", "deficiencia de Ca, Mg, K"],
        "amendments": ["cal dolomítica 2-4 ton/ha", "roca fosfórica o fosfato soluble", "silicato de Ca (escoria)", "micronutrientes"],
        "fertility": "baja (alta fijación de nutrientes)",
        "score_boost": ["ferralsol", "oxisol", "rojo intenso", "laterita", "tropical profundo"]
    },
    {
        "id": "calcisol_caliche",
        "name": "Calcisol (Suelo calcáreo con caliche)",
        "fao_class": "Calcisol", "usda_class": "Aridisols (Calcids)",
        "type_keys": ["calcáreo", "franco", "arenoso"],
        "color_keywords": ["blanco grisáceo", "gris claro", "pálido", "crema", "gris", "pedregoso blanquecino"],
        "ph_range": [7.5, 8.8], "ph_typical": 8.2,
        "clay_pct": [8, 30], "sand_pct": [25, 55],
        "compaction_typical": "alta",
        "drainage_typical": "moderado",
        "organic_matter_level": "bajo",
        "salinity_risk": "media",
        "regions": ["semillero Norte México", "Coahuila", "Nuevo León", "Tamaulipas seco", "Oaxaca semiárido", "Mediterráneo", "Oriente Medio"],
        "visual_markers": ["nódulos blancos de CaCO3 visibles", "efervescencia con ácido", "costras endurecidas blancas en profundidad", "color claro con manchas blancas"],
        "typical_crops": ["agave tequilero", "vid", "olivo", "trigo de invierno", "girasol", "garbanzo"],
        "native_plants": ["agave (Agave americana)", "palma samandoca (Yucca carnerosana)", "huizache (Acacia farnesiana)", "mezquite (Prosopis glandulosa)"],
        "issues": ["clorosis férrica por pH alto", "inmovilización de P, Fe, Mn, Zn", "costras impermeables (caliche)"],
        "amendments": ["azufre elemental 300-600 kg/ha", "ácido sulfúrico diluido en riego", "quelatos de Fe y Zn", "romper caliche con subsolador"],
        "fertility": "media (limitada por pH)",
        "score_boost": ["caliche", "calcisol", "calcáreo", "nódulos blancos", "efervescencia", "alcalino"]
    },
    {
        "id": "gleysol_hydro",
        "name": "Gleysol (Hidromórfico / suelo encharcado)",
        "fao_class": "Gleysol", "usda_class": "Mollisols/Entisols (Aquic)",
        "type_keys": ["arcilloso", "franco_arcilloso", "limoso"],
        "color_keywords": ["gris azulado", "gris verdoso", "verde grisáceo", "manchas rojizas y grises", "moteado", "azul grisáceo"],
        "ph_range": [4.5, 7.5], "ph_typical": 5.8,
        "clay_pct": [25, 60], "sand_pct": [5, 25],
        "compaction_typical": "alta",
        "drainage_typical": "pobre",
        "organic_matter_level": "medio",
        "salinity_risk": "variable",
        "regions": ["zonas con manto freático alto", "planicie costera Golfo México", "Tabasco", "Campeche pantanoso", "deltas de ríos", "cuencas endorreicas"],
        "visual_markers": ["colores grises a azul-verdoso (reducción de Fe)", "manchas óxido-rojizas (mottles)", "suelo saturado en profundidad", "olor a reducción"],
        "typical_crops": ["arroz de temporal", "caña con drenaje", "tule para artesanías", "pasto paragüas"],
        "native_plants": ["tule (Typha domingensis)", "carrizal (Phragmites communis)", "mangle rojo (Rhizophora mangle)", "popal (Thalia geniculata)"],
        "issues": ["anaerobiosis", "toxicidad de Fe y Mn reducidos", "emisión de CH4 y H2S", "difícil laboreo"],
        "amendments": ["drenaje subsuperficial", "cal viva para emergencias", "evitar laboreo en saturación", "arroz o acuicultura si no drenable"],
        "fertility": "variable",
        "score_boost": ["gleysol", "encharcado", "gris azul", "hidromórfico", "manto freático", "pantano"]
    },
    {
        "id": "xerosol_arid",
        "name": "Xerosol / Yermosol (Semiárido gris-pardo)",
        "fao_class": "Calcisol/Gypsisol", "usda_class": "Aridisols",
        "type_keys": ["franco_arenoso", "arenoso", "franco"],
        "color_keywords": ["gris pardo", "pardo claro", "amarillento", "gris tierra", "ocre seco", "desértico"],
        "ph_range": [7.0, 8.5], "ph_typical": 7.8,
        "clay_pct": [5, 22], "sand_pct": [35, 65],
        "compaction_typical": "media",
        "drainage_typical": "bueno",
        "organic_matter_level": "muy bajo",
        "salinity_risk": "media",
        "regions": ["norte árido México", "Durango", "Zacatecas seco", "altiplano semiárido", "desierto de Chihuahua", "Sonora interior"],
        "visual_markers": ["color claro y apagado", "sin estructura visible", "costra superficial fina", "muy poca vegetación", "suelo reseco y pulverulento"],
        "typical_crops": ["nopal forrajero", "agave salmiana", "frijol de temporal", "maíz con lluvia", "chile"],
        "native_plants": ["lechuguilla (Agave lechuguilla)", "sotol (Dasylirion wheeleri)", "ocotillo (Fouquieria splendens)", "orégano (Lippia graveolens)"],
        "issues": ["baja materia orgánica", "riesgo de erosión eólica", "costras superficiales", "baja actividad biológica"],
        "amendments": ["abono verde", "compost", "siembra directa", "maguey y nopal para retener suelo"],
        "fertility": "muy baja a baja",
        "score_boost": ["xerosol", "yermosol", "árido", "semiárido", "seco gris", "norte México"]
    },
    {
        "id": "nitisol_rich",
        "name": "Nitisol (Café rojizo profundo, muy fértil)",
        "fao_class": "Nitisol", "usda_class": "Alfisols",
        "type_keys": ["arcilloso", "franco_arcilloso"],
        "color_keywords": ["café rojizo", "rojo café", "rojo oscuro profundo", "marrón rojizo"],
        "ph_range": [5.5, 7.0], "ph_typical": 6.3,
        "clay_pct": [30, 55], "sand_pct": [15, 30],
        "compaction_typical": "baja",
        "drainage_typical": "bueno",
        "organic_matter_level": "medio",
        "salinity_risk": "baja",
        "regions": ["áreas cafetaleras tropicales", "Chiapas", "Oaxaca", "Veracruz productivo", "África oriental", "tierras altas tropicales"],
        "visual_markers": ["color rojizo profundo en todo el perfil", "sin grietas en seco", "buena estructura granular cúbica", "suave al tacto"],
        "typical_crops": ["café arábica", "cacao", "macadamia", "pimienta negra", "aguacate hass"],
        "native_plants": ["jobo (Spondias mombin)", "chalahuite (Inga latibracteata)", "aguacate criollo (Persea drymifolia)"],
        "issues": ["acidez moderada", "puede tener Al+3 tóxico en profundidad"],
        "amendments": ["encalado moderado", "fósforo en superficie", "materia orgánica para mantener estructura"],
        "fertility": "alta",
        "score_boost": ["nitisol", "café rojizo", "cafetalero", "África oriental", "profundo rojizo"]
    },
    {
        "id": "sandy_loam_fertile",
        "name": "Franco Arenoso (Equilibrado ligero)",
        "fao_class": "Cambisol/Regosol", "usda_class": "Inceptisols/Entisols",
        "type_keys": ["franco_arenoso"],
        "color_keywords": ["pardo", "marrón claro", "café", "crema parda", "ocre moderado"],
        "ph_range": [5.5, 7.5], "ph_typical": 6.5,
        "clay_pct": [8, 18], "sand_pct": [50, 70],
        "compaction_typical": "baja",
        "drainage_typical": "bueno",
        "organic_matter_level": "bajo a medio",
        "salinity_risk": "baja",
        "regions": ["zonas templadas húmedas", "lomeríos", "valles intermedios", "toda América Latina"],
        "visual_markers": ["grano visible pero con cohesión moderada", "suave al apretar", "se desmorona fácilmente"],
        "typical_crops": ["hortalizas diversas", "maíz", "frijol", "melón", "tomate"],
        "native_plants": ["copal (Bursera copallifera)", "nanche (Byrsonima crassifolia)", "guamúchil (Pithecellobium dulce)"],
        "issues": ["retención de agua moderada-baja", "riesgo de compactación superficial"],
        "amendments": ["compost regular", "riego frecuente", "mulching para conservar humedad"],
        "fertility": "media",
        "score_boost": ["franco arenoso", "ligero", "equilibrado", "hortalizas"]
    },
    {
        "id": "clay_loam_valley",
        "name": "Franco Arcilloso (Valle agrícola)",
        "fao_class": "Cambisol/Vertisol", "usda_class": "Inceptisols",
        "type_keys": ["franco_arcilloso"],
        "color_keywords": ["pardo oscuro", "marrón grisáceo", "gris pardo", "café oscuro grisáceo"],
        "ph_range": [6.0, 7.8], "ph_typical": 7.0,
        "clay_pct": [27, 40], "sand_pct": [20, 40],
        "compaction_typical": "media",
        "drainage_typical": "moderado",
        "organic_matter_level": "medio",
        "salinity_risk": "baja a media",
        "regions": ["valles agrícolas templados", "México centro", "Bajío", "Tlaxcala", "Puebla"],
        "visual_markers": ["terrones moderados", "cierta plasticidad húmedo", "grano fino sin visible"],
        "typical_crops": ["maíz", "trigo", "cebada", "frijol", "haba", "papa"],
        "native_plants": ["encino (Quercus rugosa)", "fresno (Fraxinus uhdei)", "tepozán (Buddleja cordata)"],
        "issues": ["laboreo difícil en húmedo", "compactación con maquinaria"],
        "amendments": ["materia orgánica", "subsolado cada 3-4 años", "no laborear saturado"],
        "fertility": "media a alta",
        "score_boost": ["franco arcilloso", "valle", "templado", "agrícola típico"]
    },
    {
        "id": "silt_loam_river",
        "name": "Franco Limoso (Ribereño suave)",
        "fao_class": "Fluvisol/Cambisol", "usda_class": "Entisols",
        "type_keys": ["limoso", "franco"],
        "color_keywords": ["gris pálido", "pardo claro", "beige grisáceo", "gris suave", "blanquecino suave"],
        "ph_range": [5.8, 7.5], "ph_typical": 6.8,
        "clay_pct": [10, 27], "sand_pct": [15, 35],
        "compaction_typical": "media",
        "drainage_typical": "moderado",
        "organic_matter_level": "medio",
        "salinity_risk": "baja",
        "regions": ["orillas de ríos lentos", "valles aluviales suaves", "Morelos", "Sinaloa agrícola"],
        "visual_markers": ["muy suave y sedoso al tacto", "forma costras finas al secar", "aspecto uniforme y fino"],
        "typical_crops": ["arroz", "hortalizas de hoja", "cebolla", "ajo", "espinaca"],
        "native_plants": ["jarilla (Senecio salignus)", "jarilla (Baccharis glutinosa)", "sauce (Salix humboldtiana)"],
        "issues": ["encostramiento superficial", "baja permeabilidad en lluvias intensas", "erosión laminar"],
        "amendments": ["materia orgánica para estructura", "cobertura vegetal permanente", "riego por surco suave"],
        "fertility": "media a alta",
        "score_boost": ["limoso", "sedoso", "limo", "fino", "ribereño", "sedimento fino"]
    },
    {
        "id": "tepetate_durisol",
        "name": "Tepetate / Durisol (Horizonte endurecido)",
        "fao_class": "Durisol/Leptosol", "usda_class": "Andisols degradados",
        "type_keys": ["franco", "arcilloso"],
        "color_keywords": ["blanco grisáceo", "gris claro duro", "crema endurecido", "pálido compacto"],
        "ph_range": [6.5, 8.0], "ph_typical": 7.3,
        "clay_pct": [20, 40], "sand_pct": [20, 40],
        "compaction_typical": "muy alta",
        "drainage_typical": "pobre",
        "organic_matter_level": "muy bajo",
        "salinity_risk": "media",
        "regions": ["altiplano central México", "Tlaxcala", "Puebla", "Hidalgo", "Estado de México", "zonas erosionadas de México"],
        "visual_markers": ["muy duro, no se rompe a mano", "aspecto blanquecino endurecido", "sin raíces penetrando", "capa dura a poca profundidad"],
        "typical_crops": ["nopal tunero (con preparación)", "maguey pulquero", "cebada con labranza especial"],
        "native_plants": ["maguey pulquero (Agave salmiana)", "biznagas (Ferocactus latispinus)", "sotol (Dasylirion acrotiche)"],
        "issues": ["impedimento físico radical", "bajísima productividad sin rehabilitación", "impermeabilidad total"],
        "amendments": ["roturado profundo con subsolador", "explosivos agrícolas para capas muy duras", "incorporar materia orgánica gruesa", "plantas pioneras de raíz pivotante"],
        "fertility": "muy baja",
        "score_boost": ["tepetate", "durisol", "duro", "endurecido", "cangahua", "horizonte petrocálcico"]
    },
    {
        "id": "black_clay_corn",
        "name": "Barro Negro (Suelo negro arcilloso fértil)",
        "fao_class": "Phaeozem/Vertisol", "usda_class": "Mollisols/Vertisols",
        "type_keys": ["arcilloso", "franco_arcilloso"],
        "color_keywords": ["negro", "negro profundo", "muy oscuro", "negro con grietas", "negro azabache"],
        "ph_range": [6.0, 7.8], "ph_typical": 7.0,
        "clay_pct": [35, 65], "sand_pct": [5, 20],
        "compaction_typical": "media",
        "drainage_typical": "moderado",
        "organic_matter_level": "alto",
        "salinity_risk": "baja a media",
        "regions": ["Oaxaca Valles Centrales", "Veracruz central", "Chiapas altiplano", "Guerrero sierra", "zona maicera México"],
        "visual_markers": ["negro profundo", "muy adherente en húmedo", "muy duro en seco", "grietas moderadas en estiaje"],
        "typical_crops": ["maíz criollo", "frijol negro", "calabaza", "chile negro", "amaranto"],
        "native_plants": ["copal (Bursera bipinnata)", "ceiba (Ceiba aesculifolia)", "guajes (Leucaena leucocephala)", "palo bobo (Ipomoea murucoides)"],
        "issues": ["labrado difícil en ambos extremos de humedad", "riesgo de anegamiento en lluvias intensas"],
        "amendments": ["siembra en camellones", "cultivos mixtos", "abonos verdes locales", "tracción animal preferible"],
        "fertility": "alta",
        "score_boost": ["barro negro", "negro oaxaqueño", "maicero", "fértil negro"]
    }
]


DATABASE_URL  = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES  = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2
    import psycopg2.extras


def get_db():
    if USE_POSTGRES:
        return psycopg2.connect(DATABASE_URL, sslmode="require")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def db_execute(conn, query, params=()):
    """
    Ejecuta una consulta con el mismo texto SQL (placeholders '?') sin importar
    el backend: en PostgreSQL los traduce a '%s' automáticamente. El cursor
    devuelto soporta acceso tipo diccionario (fila["columna"]) en ambos casos.
    """
    if USE_POSTGRES:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query.replace("?", "%s"), params)
    else:
        cur = conn.execute(query, params)
    return cur


def _init_precision_tables(conn):
    """v3.7 · perfiles medidos (para el «perfil medido más cercano») y calibración de la IA frente a mediciones reales."""
    pk   = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    real = "DOUBLE PRECISION" if USE_POSTGRES else "REAL"
    now  = "TIMESTAMP DEFAULT NOW()" if USE_POSTGRES else "TEXT DEFAULT (datetime('now'))"
    db_execute(conn, f"""CREATE TABLE IF NOT EXISTS perfiles_medidos (
        id {pk}, lat {real} NOT NULL, lng {real} NOT NULL,
        ph {real}, ec {real}, om {real}, clay {real}, sand {real}, silt {real}, cec {real}, p {real}, k {real}, n {real},
        profundidad TEXT, fuente TEXT, metodo TEXT, fecha TEXT, created_at {now}
    )""")
    db_execute(conn, "CREATE INDEX IF NOT EXISTS idx_perfiles_geo ON perfiles_medidos (lat, lng)")
    # respuestas de WoSIS (datos de laboratorio que casi no cambian): se piden una vez por zona y quedan guardadas
    db_execute(conn, f"CREATE TABLE IF NOT EXISTS wosis_cache (clave TEXT PRIMARY KEY, datos TEXT, ts {real})")
    # ídem para SoilGrids (modelo satelital: las predicciones por celda no cambian de un día a otro)
    db_execute(conn, f"CREATE TABLE IF NOT EXISTS soilgrids_cache (clave TEXT PRIMARY KEY, datos TEXT, ts {real})")
    db_execute(conn, f"""CREATE TABLE IF NOT EXISTS calibraciones (
        id {pk}, ph_ia {real}, ph_med {real}, om_ia TEXT, om_med {real}, created_at {now}
    )""")


_TABLAS_APP = ("usuarios", "perfiles_medidos", "wosis_cache", "soilgrids_cache", "calibraciones")


def _proteger_tablas_postgres(conn):
    """
    Supabase publica el esquema "public" por su API REST (anon key). Con RLS activado y SIN políticas,
    esa API no puede leer ni escribir estas tablas (la de usuarios guarda los hashes de contraseña).
    Este servidor se conecta como dueño de las tablas, así que RLS no le afecta. Es idempotente y,
    si falla (otro proveedor, permisos), solo avisa: nunca impide arrancar.
    """
    try:
        filas = db_execute(
            conn,
            "SELECT relname FROM pg_class WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace "
            "AND relname = ANY(%s) AND NOT relrowsecurity",
            (list(_TABLAS_APP),),
        ).fetchall()
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  AVISO: no se pudo revisar la protección RLS de las tablas: {e}")
        return
    for fila in filas:
        t = fila["relname"]
        if t not in _TABLAS_APP:          # nombres fijos del propio código; nunca texto del usuario
            continue
        try:
            db_execute(conn, f"ALTER TABLE public.{t} ENABLE ROW LEVEL SECURITY")
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"  AVISO: no se pudo activar RLS en {t}: {e}")


def init_db():
    conn = get_db()
    try:
        if USE_POSTGRES:
            db_execute(conn, """CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )""")
        else:
            db_execute(conn, """CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            )""")
        _init_precision_tables(conn)
        conn.commit()
        if USE_POSTGRES:
            _proteger_tablas_postgres(conn)
    finally:
        conn.close()
    backend = "PostgreSQL (persistente, producción)" if USE_POSTGRES else "SQLite (local)"
    print(f"  OK Base de datos lista — backend: {backend}")

def hash_password(p):
    """Hash con sal (scrypt; pbkdf2 si el Python no trae scrypt). Reemplaza al SHA-256 sin sal de antes."""
    try:
        return generate_password_hash(p, method="scrypt")
    except Exception:
        return generate_password_hash(p, method="pbkdf2:sha256:600000")


def _is_legacy_hash(stored):
    return not str(stored).startswith(("scrypt:", "pbkdf2:"))


def verify_password(stored, p):
    """Acepta hashes nuevos y los SHA-256 antiguos (que se migran solos al iniciar sesión)."""
    stored = str(stored or "")
    if _is_legacy_hash(stored):
        return hmac.compare_digest(stored, hashlib.sha256(p.encode()).hexdigest())
    try:
        return check_password_hash(stored, p)
    except Exception:
        return False


_DUMMY_HASH = None


def _dummy_verify(p):
    """Gasta el mismo tiempo cuando el correo no existe, para no revelar qué correos están registrados."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    verify_password(_DUMMY_HASH, p)

# ── MATCH SOIL PROFILE ────────────────────────────────────────────────────────
def _fold(t):
    """Minúsculas y sin tildes: «Calcáreo» y «calcareo» son lo mismo al comparar."""
    return "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower()) if unicodedata.category(c) != "Mn")


_LEVEL_SCALES = {
    "compaction": {"baja": (0, 0), "media": (1, 1), "alta": (2, 2), "muy alta": (3, 3)},
    "drainage": {"pobre": (0, 0), "moderado": (1, 1), "bueno": (2, 2)},
    "om": {"muy bajo": (0, 0), "bajo": (1, 1), "bajo a medio": (1, 2), "medio": (2, 2), "alto": (3, 3), "muy alto": (4, 4)},
}


def _level_pts(kind, wanted, typical, full, near):
    """Puntos por cercanía entre dos niveles ordenados (compactación, drenaje, materia orgánica): igual → full,
    a un escalón → near, más lejos → 0. «variable» vale la mitad de near."""
    sc = _LEVEL_SCALES[kind]
    w, t = sc.get(_fold(wanted)), sc.get(_fold(typical))
    if w is None:
        return None                                   # el dato del usuario no es un nivel conocido: no cuenta como evidencia
    if t is None:
        return near // 2 if _fold(typical) == "variable" else 0
    gap = max(0, max(w[0], t[0]) - min(w[1], t[1]))   # distancia entre intervalos (0 si se solapan)
    return full if gap == 0 else near if gap == 1 else 0


def match_soil_profile(vision: dict, top_n: int = 3) -> list:
    """
    Puntúa cada perfil de la BD contra lo que se sabe del suelo y devuelve los top_n más parecidos.
    El puntaje es un porcentaje (0-100) sobre lo que SÍ se pudo comparar: un dato que falta no suma ni resta, y con poca
    evidencia (p. ej. solo el tipo de suelo) el puntaje se atenúa para no aparentar certeza.
    """
    soil_type = _fold(vision.get("soilType")).replace("-", "_").replace(" ", "_").strip("_")
    color_raw = _fold(vision.get("color"))
    compaction = _fold(vision.get("compaction"))
    drainage = _fold(vision.get("drainage"))
    org_matter = _fold(vision.get("organicMatter"))
    humidity = _fold(vision.get("humidity"))
    text = f"{color_raw} {_fold(vision.get('observations'))} {_fold(vision.get('texture'))}".strip()
    try:
        ph = float(vision.get("estimatedPh"))
    except (TypeError, ValueError):
        ph = None

    scored = []
    for p in SOIL_DB:
        earned = possible = 0

        # 1. Tipo de suelo (peso más alto): exacto > mismo grupo (franco ⊂ franco_arcilloso) > misma raíz
        if soil_type:
            possible += 40
            best = 0
            for tk in p["type_keys"]:
                k = _fold(tk)
                if k == soil_type:
                    best = 40
                    break
                if len(soil_type) >= 4 and (soil_type in k or k in soil_type):
                    best = max(best, 24)
                elif k.split("_")[0] == soil_type.split("_")[0]:
                    best = max(best, 14)
            earned += best

        # 2. Color / textura visual: palabras del perfil en lo observado (y un extra por términos muy específicos)
        if text:
            possible += 18
            if any(_fold(ck) in text for ck in p["color_keywords"]):
                earned += 18
            if any(_fold(kw) in text for kw in p.get("score_boost", [])):
                earned += 12

        # 3-4. Compactación y drenaje (por cercanía entre niveles)
        for kind, wanted, typ in (("compaction", compaction, p["compaction_typical"]), ("drainage", drainage, p["drainage_typical"])):
            pts = _level_pts(kind, wanted, typ, 12, 5) if wanted else None
            if pts is not None:
                possible += 12
                earned += pts

        # 5. Materia orgánica
        if org_matter and p["organic_matter_level"]:
            pts = _level_pts("om", org_matter, p["organic_matter_level"], 8, 4)
            if pts is not None:
                possible += 8
                earned += pts

        # 6. Humedad (indicativa del drenaje)
        if humidity in ("saturado", "seco"):
            possible += 5
            if (humidity == "saturado" and p["drainage_typical"] == "pobre") or (humidity == "seco" and p["drainage_typical"] == "bueno"):
                earned += 5

        # 7. pH dentro del rango típico del perfil (o a menos de 0.5 unidades)
        if ph is not None:
            possible += 10
            lo, hi = p["ph_range"]
            earned += 10 if lo <= ph <= hi else 5 if lo - 0.5 <= ph <= hi + 0.5 else 0

        if not possible:
            continue
        # con poca evidencia el puntaje se atenúa (solo el tipo de suelo ≈ 72 como máximo); 90 puntos comparables = confianza plena
        score = round(100 * min(1.0, earned / possible) * (0.5 + 0.5 * min(1.0, possible / 90.0)))
        spec = 1.0 / max(1, len(p["type_keys"])) if soil_type and soil_type in [_fold(t) for t in p["type_keys"]] else 0.0
        scored.append((score, spec, p))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)        # a igual puntaje gana el perfil más específico para ese tipo
    return [{"score": sc, "profile": pr} for sc, _, pr in scored[:top_n] if sc > 0]

# ══════════════════════════════════════════════════════════════════════════════
#  DATOS SATELITALES DE SUELO  (v3.9)
#
#  Cadena de proveedores, en este orden (se cambia con la variable SOIL_PROVIDERS):
#    1. soildb       OpenLandMap-soildb (OpenGeoHub, 2025): mapas de 30 m, capa 0-30 cm (la zona de raíces), con
#                    pH, arcilla, arena, carbono orgánico y densidad aparente. GRATIS, sin cuenta ni claves (archivos COG
#                    en s3.opengeohub.org). Reemplaza al antiguo OpenLandMap de Earth Engine (250 m, solo superficie).
#                    CIC y nitrógeno (que soildb no publica) se completan con SoilGrids promediado a 0-30 cm.
#    2. webdav       SoilGrids 2.0 leído directo de files.isric.org (archivos VRT/COG con GDAL): 250 m, capa 0-5 cm.
#    3. wcs          SoilGrids 2.0 por WCS (maps.isric.org/mapserv): un GeoTIFF diminuto por propiedad.
#    4. rest         API REST de SoilGrids (rest.isric.org): lenta y con límite de ~5 consultas/min → SOLO último intento.
#
#  Reglas:
#   · gana el primer proveedor que responda con datos útiles; nunca se mezclan valores de dos proveedores
#     (única excepción: CIC y nitrógeno de SoilGrids completan a soildb, y cada cifra lleva anotada su fuente);
#   · soildb lee el píxel de 30 m del punto (y su vecindad inmediata si está enmascarado); la incertidumbre sale de los
#     cuantiles p16/p84 que OpenGeoHub publica a 120 m. SoilGrids se lee en su proyección Homolosine nativa, sin remuestrear;
#   · primero llegan las medias (el análisis ya puede arrancar) y después la incertidumbre de cada propiedad;
#   · cada proveedor tiene su propio disyuntor: tras 2 fallos seguidos se omite 3 min (y se recuerda tras un reinicio);
#   · si un proveedor no está disponible (falta rasterio…) se omite sin romper nada;
#   · el análisis espera como máximo _SG_FG_WAIT s; la consulta sigue en segundo plano y queda guardada
#     (memoria + base de datos): la próxima vez en esa celda sale al instante, incluso tras reiniciar.
#
#  Variables de entorno (todas opcionales):
#    SOIL_PROVIDERS=soildb,webdav,wcs,rest     proveedores y orden (quita nombres para desactivarlos; «openlandmap» = soildb)
#    SOIL_HTTP_TIMEOUT · SOIL_PHASE_BUDGET · SOILGRIDS_WAIT · SOILGRIDS_READ_TIMEOUT    tiempos en segundos
# ══════════════════════════════════════════════════════════════════════════════
# factor de escala de SoilGrids (valor entero del mapa ÷ factor = unidad convencional) y clave/decimales de salida
_SG_FACTORS = {"phh2o": 10, "clay": 10, "sand": 10, "silt": 10, "soc": 100, "bdod": 100, "cec": 10, "nitrogen": 100}
_SG_KEYS = {"phh2o": ("ph_real", 1), "clay": ("clay_pct", 1), "sand": ("sand_pct", 1), "silt": ("silt_pct", 1),
            "soc": ("organic_carbon_pct", 2), "bdod": ("bulk_density_g_cm3", 2), "cec": ("cec_cmolkg", 1),
            "nitrogen": ("nitrogen_g_kg", 2)}
_SG_CORE = ("phh2o", "clay", "sand", "silt", "soc")                  # versión ligera de la API REST
_SG_UNCERTAIN = ("phh2o", "clay", "sand", "silt", "soc", "cec")      # propiedades cuya incertidumbre usa _sg_entries
# rangos físicamente posibles: un valor fuera de ellos delata un error de escala/unidad y se descarta en vez de usarse
_SG_RANGE = {"ph_real": (2.5, 11.5), "clay_pct": (0, 100), "sand_pct": (0, 100), "silt_pct": (0, 100),
             "organic_carbon_pct": (0, 60), "bulk_density_g_cm3": (0.05, 2.2), "cec_cmolkg": (0, 250),
             "nitrogen_g_kg": (0, 40)}
_SG_NODATA = -32768                                                   # «sin dato» de los mapas SoilGrids (int16)
# En pH y densidad aparente un 0 es imposible: es el relleno de una zona enmascarada del mapa, no una medición. El lector
# lo trata como «sin dato» y toma la mediana de los píxeles vecinos con dato real (en vez de perder el dato satelital).
_SG_ZERO_NODATA = ("phh2o", "bdod")

_SG_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"
_SG_WCS_URL = "https://maps.isric.org/mapserv?map=/map/{prop}.map"
_SG_WEBDAV_URL = "https://files.isric.org/soilgrids/latest/data/{prop}/{prop}_{depth}_{stat}.vrt"
# OpenLandMap-soildb (OpenGeoHub): COG globales de 30 m en EPSG:4326, capa 0-30 cm, predicción 2020-2022. Los cuantiles p16/p84
# (≈ ±1 desviación estándar) solo existen a 120 m. «scale» = escala del catálogo (valor entero × scale = unidad física);
# si el propio archivo declara una escala, esa manda. Catálogo: github.com/openlandmap/soildb/tables/OpenLandMap_soildb_COGS.csv
_OLM_URL = ("https://s3.opengeohub.org/global-soil/global_soil_props_{folder}/"
            "{name}_{stat}_{res}_b0cm..30cm_20200101_20221231_g_epsg.4326_{tag}.tif")
_OLM_LAYERS = {
    "phh2o": dict(name="ph.h2o_iso.10390.2021.index", folder="v20250204_mosaics", tag="v20250204", scale=0.1, zero=True),
    "clay": dict(name="clay.tot_iso.11277.2020.wpct", folder="v20250523", tag="v20250523", scale=1.0, zero=False),
    "sand": dict(name="sand.tot_iso.11277.2020.wpct", folder="v20250523", tag="v20250523", scale=1.0, zero=False),
    "soc": dict(name="oc_iso.10694.1995.wpml", folder="v20250204_mosaics", tag="v20250204", scale=0.1, zero=False),
    "bdod": dict(name="bd.core_iso.11272.2017.g.cm3", folder="v20250204_mosaics", tag="v20250204", scale=0.01, zero=True),
}
_OLM_UNCERTAIN = ("phh2o", "clay", "sand", "soc")
# SoilGrids completa lo que soildb no publica (CIC y nitrógeno) promediando sus capas 0-5, 5-15 y 15-30 cm (grosor 5/10/15 cm)
_SG_SUPPLEMENT = ("cec", "nitrogen")
_SG_DEPTHS = (("0-5cm", 5.0), ("5-15cm", 10.0), ("15-30cm", 15.0))
_SG_IGH = "+proj=igh +lat_0=0 +lon_0=0 +datum=WGS84 +units=m +no_defs"        # Interrupted Goode Homolosine (la de SoilGrids)
_SG_IGH_URI = "http://www.opengis.net/def/crs/EPSG/0/152160"                  # el mismo CRS, como lo nombra el WCS de ISRIC

_SG_ALL = ("soildb", "webdav", "wcs", "rest")
_SG_LABEL = {
    "soildb": "OpenLandMap-soildb (OpenGeoHub, modelo 30 m)",
    "webdav": "ISRIC SoilGrids 2.0 (WebDAV, modelo 250 m)",
    "wcs": "ISRIC SoilGrids 2.0 (WCS, modelo 250 m)",
    "rest": "ISRIC SoilGrids 2.0 (API REST, modelo 250 m)",
}
_SG_RES = {"soildb": 30, "webdav": 250, "wcs": 250, "rest": 250}                 # resolución del modelo (m)
_SG_SUPP_LABEL = "ISRIC SoilGrids 2.0, promedio 0-30 cm (modelo 250 m)"


def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


_SG_FG_WAIT = _envf("SOILGRIDS_WAIT", 25)                # el análisis espera como máximo estos segundos por los datos satelitales
_SG_READ_TIMEOUT = _envf("SOILGRIDS_READ_TIMEOUT", 45)   # solo API REST (último intento): lectura máxima de la consulta completa
_SG_HTTP_TIMEOUT = _envf("SOIL_HTTP_TIMEOUT", 12)        # WebDAV / WCS: tiempo máximo de cada lectura
_SG_PHASE_BUDGET = _envf("SOIL_PHASE_BUDGET", 15)        # WebDAV / WCS: tope de cada fase (medias, luego cuantiles)
_SG_UPGRADE_WAIT = 6.0           # con datos básicos ya listos, espera hasta N s más por la versión completa
_SG_DB_TTL = 30 * 86400
_SG_PARTIAL_TTL = 1800           # datos sin incertidumbre / versión ligera: se guardan solo 30 min y luego se reintenta
_SG_FALLBACK_TTL = 3 * 3600      # datos de SoilGrids (respaldo de soildb): se guardan 3 h y luego se reintenta con soildb
_SG_BREAKER_SECS = 180           # tras 2 fallos seguidos de un proveedor se omite este tiempo
_SG_CRASH_PAUSE = (3600, 6 * 3600, 24 * 3600)   # si el lector de mapas se cae de golpe (error nativo): pausa creciente del proveedor
_SG_GRID = 0.0003                # ~33 m ≈ el píxel de soildb (30 m): todo punto de la misma celda comparte la consulta
_SG_DBKEY = "s2"                 # versión de la clave guardada (la rejilla cambió de ~220 m a ~33 m: las claves viejas no sirven)


class _SoilProviderError(Exception):
    """Fallo de un proveedor (red, tiempo agotado, respuesta inválida). «Sin dato en este punto» NO es un fallo."""


class _SoilReaderCrash(_SoilProviderError):
    """El proceso lector de mapas se cayó de golpe (error nativo de GDAL). Repetir de inmediato fallaría igual."""


def _sg_order():
    names = [n.strip().lower() for n in os.environ.get("SOIL_PROVIDERS", "").split(",") if n.strip()]
    names = ["soildb" if n == "openlandmap" else n for n in names]          # nombre antiguo (Earth Engine) → su reemplazo gratuito
    order = [n for n in dict.fromkeys(names) if n in _SG_ALL]
    return order or list(_SG_ALL)


# ── Resultado común a todos los proveedores ────────────────────────────────────────────────────────────────
def _sg_usable(r: dict) -> bool:
    return "ph_real" in r or ("clay_pct" in r and "sand_pct" in r)


def _sg_sane(r: dict) -> None:
    """Descarta lo que está fuera de rango físico (p. ej. por un error de escala), avisando en el registro."""
    for key, (lo, hi) in _SG_RANGE.items():
        if key in r and not (lo <= r[key] <= hi):
            print(f"[Suelo] valor fuera de rango descartado: {key} = {r[key]}")
            del r[key]


def _sg_finish(r: dict, provider: str, depth: str) -> dict:
    if all(k in r for k in ("clay_pct", "sand_pct", "silt_pct")):
        r["texture_class"] = _texture_class(r["clay_pct"], r["sand_pct"], r["silt_pct"])
    r["source"] = _SG_LABEL[provider]
    r["provider"] = provider
    r["kind"] = "modelado"
    r["depth"] = depth
    r["res_m"] = _SG_RES[provider]
    return r


def _sg_build_result(raw: dict, rng: dict, provider: str):
    """
    raw: {propiedad: media en la escala entera de SoilGrids}; rng: {propiedad: (Q0.05, Q0.95)} en la misma escala.
    Devuelve el diccionario que consume el resto de la app, o None si no alcanza para un análisis útil.
    """
    result, sd, rg = {}, {}, {}
    tex = ("clay", "sand", "silt")
    if all(raw.get(p) == 0 for p in tex):          # arcilla = arena = limo = 0 no es un suelo: es una zona enmascarada
        raw = {k: v for k, v in raw.items() if k not in tex}
    for name, (key, nd) in _SG_KEYS.items():
        if name in raw:
            f = _SG_FACTORS[name]
            result[key] = round(raw[name] / f, nd)
            if name in rng:
                lo, hi = rng[name]
                if hi >= lo:
                    rg[key] = [round(lo / f, nd), round(hi / f, nd)]
                    sd[key] = round((hi - lo) / f / 3.29, 3)      # intervalo 90 % ≈ ±1.645 σ
    _sg_sane(result)
    for key in list(sd):
        if key not in result:
            sd.pop(key, None)
            rg.pop(key, None)
    if not _sg_usable(result):
        return None
    if sd:
        result["sd"], result["range90"] = sd, rg
    return _sg_finish(result, provider, "0-5 cm")


# ── Lectura de un punto en rásters SoilGrids (WebDAV y WCS) ─────────────────────────────────────────────────
# GDAL (la librería nativa que usa rasterio) NO lanza una excepción de Python cuando falla de verdad (memoria, DLL,
# conexión cortada a medias…): tumba el PROCESO ENTERO, y con él el servidor web, sin dejar ni una línea en la consola.
# En el navegador eso se ve como «Fallo de conexión con la IA» justo al generar el informe. Por eso toda la lectura de
# rásters se hace en un PROCESO APARTE («trabajador»): si cae o se cuelga, solo falla ese proveedor, el servidor sigue
# vivo y la cadena pasa al siguiente (soildb → WebDAV → WCS → REST). El servidor ya no carga rasterio/GDAL en su memoria.
_SG_RIO = {}
_SG_RIO_LOCK = threading.Lock()
_SG_RIO_RETRY = 300                                  # si la prueba de arranque falla, se repite pasados 5 min
_SG_WORKER = {"path": None}
_SG_WORKER_LOCK = threading.Lock()
_SG_PROC_SLOTS = threading.BoundedSemaphore(2)       # como máximo 2 trabajadores a la vez (memoria del nivel gratuito)

# Código del trabajador: se guarda en una carpeta temporal privada y se ejecuta con el mismo Python del servidor.
# Recibe una tarea JSON por stdin y escribe una línea JSON por fase en stdout (medias → cuantiles → fin).
_SG_WORKER_SRC = r'''
import sys, json, math, os, time
from concurrent.futures import ThreadPoolExecutor, wait, TimeoutError as FutTimeout


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def igh(task, lat, lng):
    from rasterio.warp import transform
    xs, ys = transform("EPSG:4326", task["igh"], [lng], [lat])
    x, y = xs[0], ys[0]
    return (x, y) if math.isfinite(x) and math.isfinite(y) else None


def configure_gdal(task):
    # Las opciones de GDAL se dan por variables de entorno, ANTES de cargar rasterio: así ningún hilo tiene que
    # modificar la configuración global de GDAL mientras otros leen (rasterio.Env(**opciones) en 8 hilos a la vez).
    os.environ.update(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".vrt,.tif",
                      GDAL_HTTP_TIMEOUT=str(int(task["http_timeout"])), GDAL_HTTP_CONNECTTIMEOUT="5",
                      GDAL_HTTP_MAX_RETRY="2", GDAL_HTTP_RETRY_DELAY="1", VSI_CACHE="TRUE")
    if sys.platform == "win32":
        os.environ["GDAL_HTTP_VERSION"] = "1.1"     # sin HTTP/2: una conexión por lectura, menos estado compartido entre hilos


def sample(ds, x, y, nodata_default, zero_invalid=False):
    # Valor del píxel del punto. Si ese píxel no tiene dato real, la mediana de los píxeles vecinos con dato: primero los
    # 8 contiguos (≈ 250 m) y, si tampoco hay, el anillo siguiente (≈ 500 m). Mejor un dato satelital cercano que ninguno.
    # zero_invalid: en pH y densidad aparente un 0 es imposible (es relleno del mapa, no una medición): cuenta como «sin dato».
    import numpy as np
    from rasterio.windows import Window
    row, col = ds.index(x, y)
    if row < 0 or col < 0 or row >= ds.height or col >= ds.width:
        return None
    r0, c0 = max(row - 2, 0), max(col - 2, 0)
    r1, c1 = min(row + 3, ds.height), min(col + 3, ds.width)
    arr = ds.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
    nodata = ds.nodata if ds.nodata is not None else nodata_default
    if nodata is None:                                            # el archivo no declara «sin dato»: el valor máximo del tipo
        nodata = {"uint8": 255, "uint16": 65535, "int16": -32768, "int32": -2147483648}.get(ds.dtypes[0])
    valid = (arr != nodata) if nodata is not None else np.ones(arr.shape, dtype=bool)
    if arr.dtype.kind == "f":
        valid &= np.isfinite(arr)
    if zero_invalid:
        valid &= arr != 0
    rr, cc = row - r0, col - c0
    if valid[rr, cc]:
        return float(arr[rr, cc])
    for k in (1, 2):
        ok = valid[max(rr - k, 0):rr + k + 1, max(cc - k, 0):cc + k + 1]
        if ok.any():
            return float(np.median(arr[max(rr - k, 0):rr + k + 1, max(cc - k, 0):cc + k + 1][ok]))
    return None


def webdav_point(task, prop, stat, x, y, depth="0-5cm"):
    import rasterio
    url = task["webdav_url"].format(prop=prop, stat=stat, depth=depth)
    if url.startswith(("http://", "https://")):
        url = "/vsicurl/" + url
    try:
        with rasterio.open(url) as ds:
            return sample(ds, x, y, task["nodata"], prop in task["zero_nodata"])
    except rasterio.errors.RasterioIOError as ex:
        raise RuntimeError(f"{prop}_{stat}: {str(ex)[:140]}")


def cog_point(task, prop, url, scale, lat, lng, zero_invalid):
    """Valor físico de un COG de OpenLandMap-soildb (EPSG:4326) en el punto. None = sin dato en ese lugar (no es un fallo)."""
    import rasterio
    try:
        with rasterio.open("/vsicurl/" + url if url.startswith(("http://", "https://")) else url) as ds:
            x, y = lng, lat
            epsg = ds.crs.to_epsg() if ds.crs else None
            if ds.crs is not None and epsg != 4326:                # por si algún mosaico viniera en otra proyección
                from rasterio.warp import transform
                xs, ys = transform("EPSG:4326", ds.crs, [lng], [lat])
                x, y = xs[0], ys[0]
            v = sample(ds, x, y, None, zero_invalid)
            if v is None:
                return None
            sc = ds.scales[0] if ds.scales else 1.0
            off = ds.offsets[0] if ds.offsets else 0.0
            if (sc and sc != 1.0) or off:                          # la escala declarada por el archivo manda
                return float(v * (sc or 1.0) + (off or 0.0))
            return float(v * scale)
    except rasterio.errors.RasterioIOError as ex:
        raise RuntimeError(f"{prop}: {str(ex)[:140]}")


def wcs_point(task, prop, stat, x, y):
    import rasterio, requests
    from urllib.parse import quote
    cov = f"{prop}_0-5cm_{stat}"
    half = 625.0                                                  # 2.5 celdas de 250 m a cada lado (ventana de ~5 × 5)
    q = [("SERVICE", "WCS"), ("VERSION", "2.0.1"), ("REQUEST", "GetCoverage"), ("COVERAGEID", cov),
         ("FORMAT", "GEOTIFF_INT16"),
         ("SUBSET", f"X({x - half:.1f},{x + half:.1f})"), ("SUBSET", f"Y({y - half:.1f},{y + half:.1f})"),
         ("SUBSETTINGCRS", task["igh_uri"]), ("OUTPUTCRS", task["igh_uri"])]
    url = task["wcs_url"].format(prop=prop) + "&" + "&".join(f"{k}={quote(v, safe='(),.:/-')}" for k, v in q)
    resp = None
    for attempt in (1, 2):
        try:
            resp = requests.get(url, timeout=(5, task["http_timeout"]))
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as ex:
            raise RuntimeError(f"{cov}: {type(ex).__name__}")
        if resp.status_code in (429, 502, 503, 504) and attempt == 1:
            time.sleep(1.0)                                       # sobrecarga momentánea: un solo reintento
            continue
        break
    body = resp.content
    if body[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+"):    # firma TIFF / BigTIFF
        try:
            with rasterio.MemoryFile(body) as mf, mf.open() as ds:
                return sample(ds, x, y, task["nodata"], prop in task["zero_nodata"])
        except rasterio.errors.RasterioIOError as ex:
            raise RuntimeError(f"{cov}: GeoTIFF ilegible ({str(ex)[:100]})")
    text = body[:600].decode("utf-8", "replace")                   # no es un TIFF: informe de excepción OGC
    if "InvalidSubsetting" in text:
        return None                                               # el punto queda fuera de la cobertura: no es un fallo
    raise RuntimeError(f"{cov}: HTTP {resp.status_code} {' '.join(text.split())[:110]}")


def run_parallel(fn, task, jobs, budget, warm=False):
    out, errs = {}, []
    if not jobs:
        return out, errs
    items = list(jobs.items())
    if warm and len(items) > 1:
        # Primera lectura SOLA: la primera vez que GDAL usa la red inicializa libcurl, SSL y PROJ, y esa inicialización
        # no admite que varios hilos la hagan a la vez (en Windows tumba el proceso). Con lo ya inicializado, el resto
        # sí corre en paralelo.
        t0 = time.time()
        key, args = items[0]
        solo = ThreadPoolExecutor(max_workers=1)
        fut = solo.submit(fn, task, *args)
        try:
            out[key] = fut.result(timeout=budget)
        except FutTimeout:
            solo.shutdown(wait=False, cancel_futures=True)
            return out, [f"{key}: sin respuesta en {budget:.0f} s"]
        except Exception as ex:
            errs.append(f"{key}: {ex}")
        solo.shutdown(wait=False)
        items = items[1:]
        budget = max(1.0, budget - (time.time() - t0))
    top = 4 if (sys.platform == "win32" and task["provider"] in ("webdav", "soildb")) else 8
    pool = ThreadPoolExecutor(max_workers=min(top, len(items)))
    futs = {pool.submit(fn, task, *args): key for key, args in items}
    done, pending = wait(list(futs), timeout=budget)
    for f in done:
        try:
            out[futs[f]] = f.result()
        except Exception as ex:
            errs.append(f"{futs[f]}: {ex}")
    for f in pending:
        f.cancel()
        errs.append(f"{futs[f]}: sin respuesta en {budget:.0f} s")
    pool.shutdown(wait=False, cancel_futures=True)
    return out, errs


def soildb_main(task):
    """OpenLandMap-soildb: medias a 30 m → cuantiles p16/p84 (120 m) → CIC y nitrógeno de SoilGrids (0-30 cm). Una línea por fase."""
    lat, lng = task["lat"], task["lng"]
    jobs = {p: (p, spec["url"], spec["scale"], lat, lng, spec["zero"]) for p, spec in task["layers"].items()}
    vals, errs = run_parallel(cog_point, task, jobs, task["phase_budget"], warm=True)
    emit({"phase": "means", "values": vals, "errors": errs})
    ok = {p for p in task["uncertain"] if vals.get(p) is not None}
    if ok:
        qjobs = {k: (k, q["url"], q["scale"], lat, lng, q["zero"]) for k, q in task["qlayers"].items() if k.split("|")[0] in ok}
        qv, qerrs = run_parallel(cog_point, task, qjobs, task["phase_budget"])
        emit({"phase": "quantiles", "values": qv, "errors": qerrs})
    sup = task.get("supplement") or {}
    sv, serrs = {}, []
    if sup.get("props") and vals:
        pt = igh(task, lat, lng)
        if pt is not None:
            sjobs = {f"{p}|{d}": (p, "mean", pt[0], pt[1], d) for p in sup["props"] for d in sup["depths"]}
            sv, serrs = run_parallel(webdav_point, task, sjobs, task["phase_budget"])
    emit({"phase": "supplement", "values": sv, "errors": serrs})          # siempre: avisa que ya no falta nada por llegar


def main():
    task = json.loads(sys.stdin.read())
    if task.get("selftest"):                                      # prueba de arranque: ¿carga rasterio y proyecta?
        import rasterio
        pt = igh(task, 19.4326, -99.1332)
        emit({"phase": "selftest", "ok": pt is not None, "rasterio": rasterio.__version__, "gdal": rasterio.__gdal_version__})
        return
    configure_gdal(task)
    if task["provider"] == "soildb":
        soildb_main(task)
        emit({"phase": "done"})
        return
    pt = igh(task, task["lat"], task["lng"])
    if pt is None:
        emit({"phase": "nopoint"})
        return
    x, y = pt
    fn = webdav_point if task["provider"] == "webdav" else wcs_point
    vals, errs = run_parallel(fn, task, {p: (p, "mean", x, y) for p in task["props"]}, task["phase_budget"],
                              warm=task["provider"] == "webdav")
    emit({"phase": "means", "values": vals, "errors": errs})
    ok = [p for p in task["uncertain"] if vals.get(p) is not None]
    if ok:
        jobs = {f"{p}|{q}": (p, q, x, y) for p in ok for q in ("Q0.05", "Q0.95")}
        qv, qerrs = run_parallel(fn, task, jobs, task["phase_budget"])
        emit({"phase": "quantiles", "values": qv, "errors": qerrs})
    emit({"phase": "done"})


try:
    main()
    code = 0
except BaseException as ex:
    try:
        emit({"phase": "error", "message": type(ex).__name__ + ": " + str(ex)[:300]})
    except Exception:
        pass
    code = 2
try:
    sys.stdout.flush()
except Exception:
    pass
os._exit(code)          # sin esperar a hilos de red lentos: terminan solos por su tiempo máximo
'''


def _sg_worker_file() -> str:
    """Ruta del script del trabajador (se escribe una vez en una carpeta temporal privada que solo contiene ese archivo)."""
    with _SG_WORKER_LOCK:
        path = _SG_WORKER["path"]
        if path and os.path.isfile(path):
            return path
        folder = tempfile.mkdtemp(prefix="ecoscan_")
        path = os.path.join(folder, "soil_worker.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_SG_WORKER_SRC.lstrip("\n"))
        atexit.register(shutil.rmtree, folder, True)
        _SG_WORKER["path"] = path
        return path


def _exit_text(rc) -> str:
    if rc is None:
        return "?"
    if rc < 0:
        return f"señal {-rc}"                       # Linux/macOS: -11 = fallo de segmentación
    return f"0x{rc:08X}" if rc > 255 else str(rc)   # Windows: 0xC0000005 = violación de acceso


def _sg_worker_stream(task: dict, budget: float):
    """
    Lanza el trabajador y entrega cada mensaje (dict) en cuanto llega. Lanza _SoilProviderError si el proceso falla,
    cae de golpe (error nativo) o no termina en `budget` s. Al cerrar el generador, el proceso se termina.
    """
    if not sys.executable:
        raise _SoilProviderError("no se encontró el intérprete de Python para lanzar el lector de mapas")
    if not _SG_PROC_SLOTS.acquire(timeout=20):
        raise _SoilProviderError("el lector de mapas está ocupado con otras consultas")
    proc = timer = None
    state = {"timeout": False}
    try:
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen(
            [sys.executable, _sg_worker_file()], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))      # en Windows, sin ventana de consola extra

        def _kill():
            state["timeout"] = True
            try:
                proc.kill()
            except OSError:
                pass

        timer = threading.Timer(budget, _kill)
        timer.daemon = True
        timer.start()
        try:
            proc.stdin.write(json.dumps(task))
            proc.stdin.close()
        except OSError:
            pass                                                           # murió al arrancar: se informa abajo con su código
        finished, tail = False, []
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                tail = (tail + [line])[-3:]                                # texto suelto (p. ej. un aviso de GDAL)
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("phase") == "error":
                raise _SoilProviderError(msg.get("message") or "error en el lector de mapas")
            if msg.get("phase") in ("done", "selftest"):
                finished = True
            yield msg
        try:
            rc = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = proc.wait()
        if not finished:
            if state["timeout"]:
                raise _SoilProviderError(f"el lector de mapas no terminó en {budget:.0f} s")
            extra = (": " + " | ".join(tail))[:220] if tail else ""
            msg = f"el lector de mapas se cerró de forma inesperada (código {_exit_text(rc)}){extra}"
            # código 1/2 = error de Python ya controlado; negativo (señal) o > 255 (p. ej. 0xC0000409) = caída nativa de GDAL
            raise (_SoilReaderCrash if (rc is not None and (rc < 0 or rc > 255)) else _SoilProviderError)(msg)
    finally:
        if timer is not None:
            timer.cancel()
        if proc is not None:
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
            for pipe in (proc.stdout, proc.stdin):
                try:
                    pipe and pipe.close()
                except Exception:
                    pass
        _SG_PROC_SLOTS.release()


def _sg_have_rasterio() -> bool:
    """¿Está instalado rasterio y el lector de mapas arranca? Se comprueba una vez (en un proceso aparte, sin cargar GDAL aquí)."""
    with _SG_RIO_LOCK:
        if _SG_RIO.get("ok") or (_SG_RIO.get("why") and time.time() < _SG_RIO.get("retry_at", 0)):
            return bool(_SG_RIO.get("ok"))
        try:
            present = importlib.util.find_spec("rasterio") is not None
        except (ImportError, ValueError):
            present = False
        if not present:
            _SG_RIO.update(ok=False, why="falta instalar rasterio (pip install rasterio)", retry_at=time.time() + _SG_RIO_RETRY)
            return False
        try:
            msgs = list(_sg_worker_stream({"selftest": True, "igh": _SG_IGH}, 40))
            info = next((m for m in msgs if m.get("phase") == "selftest"), None)
            if not info or not info.get("ok"):
                raise _SoilProviderError("la prueba de proyección no dio resultado")
            _SG_RIO.update(ok=True, why="", info=info)
        except Exception as ex:
            _SG_RIO.update(ok=False, why=f"el lector de mapas no pasó la prueba de arranque ({ex})", retry_at=time.time() + _SG_RIO_RETRY)
        return bool(_SG_RIO.get("ok"))


def _sg_raster_provider(name: str, lat: float, lng: float):
    """Proveedor por rásters (WebDAV o WCS). Entrega ("partial", medias) y después ("full", medias + incertidumbre)."""
    task = {"provider": name, "lat": lat, "lng": lng, "props": list(_SG_FACTORS), "uncertain": list(_SG_UNCERTAIN),
            "phase_budget": _SG_PHASE_BUDGET, "http_timeout": _SG_HTTP_TIMEOUT, "nodata": _SG_NODATA,
            "webdav_url": _SG_WEBDAV_URL, "wcs_url": _SG_WCS_URL, "igh": _SG_IGH, "igh_uri": _SG_IGH_URI,
            "zero_nodata": list(_SG_ZERO_NODATA)}
    raw = {}
    stream = _sg_worker_stream(task, 2 * _SG_PHASE_BUDGET + 20)
    try:
        for msg in stream:
            phase = msg.get("phase")
            if phase == "nopoint":
                return                                                     # el punto cae fuera de la proyección del mapa
            if phase == "means":
                vals, errs = msg.get("values") or {}, msg.get("errors") or []
                raw = {p: v for p, v in vals.items() if v is not None}
                if not raw:
                    if not vals:                                           # ninguna lectura respondió: el proveedor falló
                        raise _SoilProviderError(errs[0] if errs else "sin respuesta")
                    return                                                 # respondieron, pero sin dato en este punto
                if errs:
                    print(f"[Suelo] {_SG_LABEL[name]}: {len(errs)} de {len(_SG_FACTORS)} propiedades sin respuesta ({errs[0]})")
                data = _sg_build_result(raw, {}, name)
                if not data:
                    return
                yield "partial", data
            elif phase == "quantiles":
                qv = msg.get("values") or {}
                rng = {}
                for p in _SG_UNCERTAIN:
                    lo, hi = qv.get(f"{p}|Q0.05"), qv.get(f"{p}|Q0.95")
                    if lo is not None and hi is not None:
                        rng[p] = (lo, hi)
                if rng:
                    full = _sg_build_result(raw, rng, name)
                    if full:
                        yield ("full" if len(rng) == sum(1 for p in _SG_UNCERTAIN if p in raw) else "partial"), full
    finally:
        stream.close()


def _prov_webdav(lat, lng):
    yield from _sg_raster_provider("webdav", lat, lng)


def _prov_wcs(lat, lng):
    yield from _sg_raster_provider("wcs", lat, lng)


# ── OpenLandMap-soildb (OpenGeoHub): mapas de 30 m, capa 0-30 cm ───────────────────────────────────────────
# El trabajador lee el píxel del punto de cada COG y devuelve valores físicos: pH, arcilla %, arena %, carbono orgánico en g/kg
# y densidad aparente en g/cm³. Aquí se convierten a las unidades de la app y se derivan limo (100 − arcilla − arena) y rangos.
def _olm_url(prop: str, stat: str, res: str) -> str:
    L = _OLM_LAYERS[prop]
    return _OLM_URL.format(folder=L["folder"], name=L["name"], stat=stat, res=res, tag=L["tag"])


def _olm_fix_units(prop: str, v: float) -> float:
    """Rescata un error de escala evidente (poco probable): pH ×10 o densidad aparente en kg/m³ o en décimas."""
    if prop == "phh2o" and 25 <= v <= 115:
        return v / 10.0
    if prop == "bdod":
        if v >= 50:
            return v / 1000.0
        if 5 <= v < 50:
            return v / 10.0
    return v


def _olm_build_result(means: dict, qv: dict):
    """means: {prop: valor físico}; qv: {'prop|p16'/'prop|p84': valor físico}. → dict de la app o None si no alcanza."""
    vals = {}
    for prop, v in means.items():
        if v is None or not math.isfinite(v):
            continue
        fixed = _olm_fix_units(prop, float(v))
        if fixed != v:
            print(f"[Suelo] soildb: escala corregida en {prop} ({v:g} → {fixed:g})")
        vals[prop] = fixed
    if "soc" in vals:
        vals["soc"] = vals["soc"] / 10.0                              # g/kg → %
    if "clay" in vals and "sand" in vals and vals["clay"] + vals["sand"] > 105:
        print(f"[Suelo] soildb: arcilla + arena = {vals['clay'] + vals['sand']:.0f} % (imposible); se descarta la textura")
        vals.pop("clay"), vals.pop("sand")
    result, sd, rg = {}, {}, {}
    for prop, (key, nd) in _SG_KEYS.items():
        if prop in vals:
            result[key] = round(vals[prop], nd)
    sd_raw = {}
    for prop in _OLM_UNCERTAIN:
        lo, hi = qv.get(f"{prop}|p16"), qv.get(f"{prop}|p84")
        if prop in vals and lo is not None and hi is not None:
            lo, hi = _olm_fix_units(prop, lo), _olm_fix_units(prop, hi)
            if prop == "soc":
                lo, hi = lo / 10.0, hi / 10.0                         # g/kg → %
            if hi >= lo:
                sd_raw[prop] = (hi - lo) / 2.0                        # p16–p84 ≈ ±1 desviación estándar
    if "clay" in vals and "sand" in vals:
        silt = 100.0 - vals["clay"] - vals["sand"]
        result["silt_pct"] = round(min(100.0, max(silt, 0.0)), 1)
        if "clay" in sd_raw and "sand" in sd_raw:
            sd_raw["silt"] = math.hypot(sd_raw["clay"], sd_raw["sand"])
    _sg_sane(result)
    for prop, s1 in sd_raw.items():
        key = _SG_KEYS[prop][0]
        if key in result and s1 > 0:
            nd = _SG_KEYS[prop][1]
            lo_b, hi_b = _SG_RANGE[key]
            sd[key] = round(s1, 3)
            rg[key] = [round(max(lo_b, result[key] - 1.645 * s1), nd), round(min(hi_b, result[key] + 1.645 * s1), nd)]
    if not _sg_usable(result):
        return None
    if sd:
        result["sd"], result["range90"] = sd, rg
    out = _sg_finish(result, "soildb", "0-30 cm")
    out["period"] = "2020-2022"
    return out


def _olm_supplement(data: dict, sv: dict) -> dict:
    """Añade CIC y nitrógeno de SoilGrids (promedio ponderado 0-30 cm). Cada cifra queda anotada en data['supplement']."""
    note = {}
    for prop in _SG_SUPPLEMENT:
        num = den = 0.0
        for depth, w in _SG_DEPTHS:
            v = sv.get(f"{prop}|{depth}")
            if v is not None:
                num += w * float(v)
                den += w
        if den >= 10.0:                                               # al menos 10 de los 30 cm con dato
            key, nd = _SG_KEYS[prop]
            val = round(num / den / _SG_FACTORS[prop], nd)
            lo, hi = _SG_RANGE[key]
            if lo <= val <= hi:
                data[key] = val
                note[key] = _SG_SUPP_LABEL
    if note:
        data["supplement"] = note
    return data


def _prov_soildb(lat, lng):
    if not _sg_have_rasterio():
        raise _SoilProviderError(f"rasterio no disponible ({_SG_RIO.get('why', '?')})")
    layers = {p: {"url": _olm_url(p, "m", "30m"), "scale": L["scale"], "zero": L["zero"]} for p, L in _OLM_LAYERS.items()}
    qlayers = {f"{p}|{q}": {"url": _olm_url(p, q, "120m"), "scale": _OLM_LAYERS[p]["scale"], "zero": _OLM_LAYERS[p]["zero"]}
               for p in _OLM_UNCERTAIN for q in ("p16", "p84")}
    task = {"provider": "soildb", "lat": lat, "lng": lng, "layers": layers, "qlayers": qlayers,
            "uncertain": list(_OLM_UNCERTAIN), "phase_budget": _SG_PHASE_BUDGET, "http_timeout": _SG_HTTP_TIMEOUT,
            "nodata": _SG_NODATA, "webdav_url": _SG_WEBDAV_URL, "igh": _SG_IGH, "zero_nodata": list(_SG_ZERO_NODATA),
            "supplement": {"props": list(_SG_SUPPLEMENT), "depths": [d for d, _ in _SG_DEPTHS]}}
    means, data, q_complete = {}, None, False
    stream = _sg_worker_stream(task, 3 * _SG_PHASE_BUDGET + 20)
    try:
        for msg in stream:
            phase = msg.get("phase")
            if phase == "means":
                vals, errs = msg.get("values") or {}, msg.get("errors") or []
                means = {p: v for p, v in vals.items() if v is not None}
                if not means:
                    if not vals:                                      # ninguna lectura respondió: el proveedor falló
                        raise _SoilProviderError(errs[0] if errs else "sin respuesta")
                    return                                            # respondieron, pero sin dato en este punto
                if errs:
                    print(f"[Suelo] {_SG_LABEL['soildb']}: {len(errs)} de {len(_OLM_LAYERS)} propiedades sin respuesta ({errs[0]})")
                data = _olm_build_result(means, {})
                if not data:
                    return
                yield "partial", data
            elif phase == "quantiles" and data is not None:
                full = _olm_build_result(means, msg.get("values") or {})
                if full and full.get("sd"):
                    # «completo» = incertidumbre de todas las propiedades que tienen cuantiles; si falta alguna, se reintenta luego
                    need = sum(1 for p in _OLM_UNCERTAIN if p in means)
                    got = sum(1 for k in full["sd"] if k in ("ph_real", "clay_pct", "sand_pct", "organic_carbon_pct"))
                    data, q_complete = full, got >= need
                    yield "partial", data                             # todavía falta el complemento (CIC y nitrógeno)
            elif phase == "supplement" and data is not None:
                merged = _olm_supplement(dict(data), msg.get("values") or {})
                if merged.get("supplement"):
                    data = merged
                elif msg.get("errors"):
                    print(f"[Suelo] CIC y nitrógeno de SoilGrids sin respuesta ({msg['errors'][0]}); se reintentará más tarde")
                    q_complete = False                                # se guarda poco tiempo para volver a pedirlos
                yield ("full" if q_complete else "partial"), data
    finally:
        stream.close()


# ── API REST de SoilGrids: último intento ──────────────────────────────────────────────────────────────────
def _sg_rest_query(lat: float, lng: float, props=None, quantiles: bool = True, timeout=None):
    """
    Consulta la API REST de ISRIC. OJO: es lenta (puede pasar de 45 s con 8 propiedades y cuantiles) y limita a ~5 consultas
    por minuto; por eso es el último recurso. Si el servidor rechaza los cuantiles se reintenta solo con la media.
    """
    props = list(props or _SG_FACTORS)
    base = {"lon": round(lng, 6), "lat": round(lat, 6), "property": props, "depth": ["0-5cm"]}
    resp = None
    for values in ((["mean", "Q0.05", "Q0.95"], ["mean"]) if quantiles else (["mean"],)):
        try:
            resp = _req.get(_SG_URL, params={**base, "value": values}, timeout=(5, timeout or _SG_READ_TIMEOUT))
        except (_req.exceptions.Timeout, _req.exceptions.ConnectionError) as ex:
            raise _SoilProviderError(f"REST: {type(ex).__name__}") from ex
        if resp.status_code not in (400, 422, 500):
            break
    if resp is None or resp.status_code != 200:
        raise _SoilProviderError(f"REST: HTTP {resp.status_code if resp is not None else '?'}")
    try:
        layers = resp.json().get("properties", {}).get("layers", [])
    except ValueError as ex:
        raise _SoilProviderError("REST: respuesta que no es JSON") from ex
    raw, rng = {}, {}
    for layer in layers:
        name, depths = layer.get("name"), layer.get("depths", [])
        if name in _SG_FACTORS and depths:
            vals = depths[0].get("values", {}) or {}
            if vals.get("mean") is not None:
                raw[name] = vals["mean"]
                if vals.get("Q0.05") is not None and vals.get("Q0.95") is not None:
                    rng[name] = (vals["Q0.05"], vals["Q0.95"])
    return _sg_build_result(raw, rng, "rest") if raw else None


def _prov_rest(lat, lng):
    try:
        d = _sg_rest_query(lat, lng)
    except _SoilProviderError as ex:
        print(f"[Suelo] REST: consulta completa sin éxito ({ex}); se intenta la ligera (pH, textura y carbono)")
        d = _sg_rest_query(lat, lng, props=_SG_CORE, quantiles=False, timeout=min(20.0, _SG_READ_TIMEOUT))
        if d:
            yield "partial", d
        return
    if d:
        yield "full", d


_SG_PROVIDERS = {"soildb": _prov_soildb, "webdav": _prov_webdav, "wcs": _prov_wcs, "rest": _prov_rest}


# ── Disponibilidad, disyuntor por proveedor y salud guardada ────────────────────────────────────────────────
_SG_CACHE = {}
_SG_LOCK = threading.RLock()
_SG_INFLIGHT = {}                # celda → consulta en curso {"ready": ya hay datos, "full": versión completa, "event": terminó, "data": resultado}
_SG_STATE = {}                   # proveedor → {"fails": fallos seguidos, "down_until": hasta cuándo se omite}
_SG_HEALTH = {"loaded": False}
_SG_WARNED = set()


def _sg_available(name: str):
    """(ok, motivo) de un proveedor, sin tocar la red."""
    if name in ("soildb", "webdav", "wcs"):
        return (True, "") if _sg_have_rasterio() else (False, f"rasterio no disponible ({_SG_RIO.get('why', '?')})")
    return True, ""


def _sg_down(name: str) -> bool:
    with _SG_LOCK:
        st = _SG_STATE.get(name)
        return bool(st and time.time() < st["down_until"])


def _sg_all_down() -> bool:
    return all((not _sg_available(n)[0]) or _sg_down(n) for n in _sg_order())


def _sg_health_save():
    now = time.time()
    with _SG_LOCK:
        down = {n: s["down_until"] for n, s in _SG_STATE.items() if s["down_until"] > now}
    _geo_db_put("soilgrids_cache", "_health", {"down": down, "t": now})


def _sg_health_load():
    """Una vez por arranque: si un proveedor estaba en pausa (falló hace menos de 3 min), sigue en pausa tras reiniciar."""
    if _SG_HEALTH["loaded"]:
        return
    _SG_HEALTH["loaded"] = True
    h = _geo_db_get("soilgrids_cache", "_health")
    if not (isinstance(h, dict) and isinstance(h.get("down"), dict)):
        return
    now = time.time()
    with _SG_LOCK:
        for n, until in h["down"].items():
            try:
                until = float(until)
            except (TypeError, ValueError):
                continue
            if n in _SG_ALL and until > now:
                _SG_STATE[n] = {"fails": 2, "down_until": until}
                print(f"[Suelo] {_SG_LABEL[n]} falló hace poco: se omite durante {_fmt_pause(until - now)} más")


def _fmt_pause(secs: float) -> str:
    return f"{secs / 3600:g} h" if secs >= 3600 else f"{max(1, round(secs / 60))} min"


_SG_CRASHES = {}                 # proveedor → veces que su lector de mapas se cayó de golpe (sube la pausa cada vez)


def _sg_pause(name: str) -> None:
    """El lector de mapas del proveedor se cayó de golpe: se omite un buen rato (1 h, luego 6 h, luego 24 h) y se recuerda al reiniciar."""
    with _SG_LOCK:
        n = _SG_CRASHES.get(name, 0)
        _SG_CRASHES[name] = n + 1
        secs = _SG_CRASH_PAUSE[min(n, len(_SG_CRASH_PAUSE) - 1)]
        _SG_STATE[name] = {"fails": 2, "down_until": time.time() + secs}
    print(f"[Suelo] {_SG_LABEL[name]}: el lector de mapas se cae en este equipo; se omite {_fmt_pause(secs)} y se usa el siguiente proveedor.")
    if n == 0:
        keep = [x for x in _sg_order() if x not in _SG_CRASHES]
        print(f"[Suelo] Para no volver a probarlo, agrega a tu archivo .env:  SOIL_PROVIDERS={','.join(keep)}")
    _sg_health_save()


def _sg_mark(name: str, ok: bool) -> None:
    changed = False
    with _SG_LOCK:
        st = _SG_STATE.setdefault(name, {"fails": 0, "down_until": 0.0})
        if ok:
            changed = st["down_until"] > 0
            st["fails"], st["down_until"] = 0, 0.0
            _SG_CRASHES.pop(name, None)
        else:
            st["fails"] += 1
            if st["fails"] >= 2:
                st["down_until"] = time.time() + _SG_BREAKER_SECS
                changed = True
                print(f"[Suelo] {_SG_LABEL[name]}: 2 fallos seguidos; se omite durante {_SG_BREAKER_SECS // 60} min")
    if changed:
        _sg_health_save()


def _sg_provider_report():
    """[(nombre, disponible, nota)] en el orden de uso; se imprime al arrancar."""
    rep = []
    for n in _sg_order():
        ok, why = _sg_available(n)
        rep.append((n, ok, "" if ok else why))
    return rep


def _geo_db_get(table, key):
    """Dato guardado en la base de datos (tabla interna wosis_cache / soilgrids_cache) o None si falta o caducó."""
    try:
        conn = get_db()
        try:
            row = db_execute(conn, f"SELECT datos, ts FROM {table} WHERE clave = ?", (key,)).fetchone()
        finally:
            conn.close()
        if row and time.time() - float(row["ts"]) < (_SG_DB_TTL if table == "soilgrids_cache" else _WOSIS_DB_TTL):
            return json.loads(row["datos"])
    except Exception as ex:
        print(f"[{table}] caché guardada no disponible: {ex}")
    return None


def _geo_db_put(table, key, data, ttl=None):
    """Guarda en la base de datos. ttl (s) acorta la vigencia de esta entrada (p. ej. datos parciales)."""
    full_ttl = _SG_DB_TTL if table == "soilgrids_cache" else _WOSIS_DB_TTL
    ts = time.time() - (full_ttl - ttl) if ttl else time.time()
    try:
        conn = get_db()
        try:
            db_execute(conn, f"INSERT INTO {table} (clave, datos, ts) VALUES (?, ?, ?) "
                             "ON CONFLICT (clave) DO UPDATE SET datos = excluded.datos, ts = excluded.ts",
                       (key, json.dumps(data), ts))
            conn.commit()
        finally:
            conn.close()
    except Exception as ex:
        print(f"[{table}] no se pudo guardar la caché: {ex}")


# ── La cadena en segundo plano y la función que usa el análisis ─────────────────────────────────────────────
def _sg_run_chain(qlat: float, qlng: float, slot: dict):
    """Prueba los proveedores en orden. → (datos, proveedor, completo). Lo primero que responda con datos útiles gana."""
    for name in _sg_order():
        label = _SG_LABEL[name]
        ok, why = _sg_available(name)
        if not ok:
            if name not in _SG_WARNED:
                _SG_WARNED.add(name)
                print(f"[Suelo] {label}: no disponible, se omite ({why})")
            continue
        if _sg_down(name):
            continue
        t0, got, stage = time.time(), None, None
        try:
            for stage, data in _SG_PROVIDERS[name](qlat, qlng):
                got = data
                slot["data"] = data                               # el análisis ya puede usar lo que llegó
                slot["ready"].set()
                if stage == "full":
                    slot["full"].set()
        except Exception as ex:
            print(f"[Suelo] {label}: falló ({type(ex).__name__}: {ex})")
            crashed = isinstance(ex, _SoilReaderCrash)
            if got is None:
                if crashed:
                    _sg_pause(name)
                else:
                    _sg_mark(name, False)
                continue
            print(f"[Suelo] {label}: se conservan los datos que ya habían llegado")
            if crashed:
                _sg_pause(name)
                return got, name, False
        if got:
            _sg_mark(name, True)
            tail = "" if stage == "full" else " (sin la incertidumbre completa; se reintentará más tarde)"
            print(f"[Suelo] {label}: listo en {time.time() - t0:.1f} s{tail}")
            return got, name, stage == "full"
        print(f"[Suelo] {label}: sin dato para este punto; se prueba el siguiente proveedor")
    return None, None, False


def _sg_fetch_bg(key, qlat, qlng, slot):
    """Hilo en segundo plano: termina la cadena aunque el análisis ya no espere y deja el resultado guardado."""
    t0 = time.time()
    data, provider, complete = None, None, False
    try:
        data, provider, complete = _sg_run_chain(qlat, qlng, slot)
    except Exception as ex:
        print(f"[Suelo] error inesperado: {type(ex).__name__}: {ex}")
    ttl = None
    if data:
        if not complete:
            ttl = _SG_PARTIAL_TTL
        elif provider != "soildb":
            ttl = _SG_FALLBACK_TTL                            # SoilGrids solo respalda a soildb: se guarda poco y se vuelve a intentar
    with _SG_LOCK:
        if len(_SG_CACHE) > 500:
            _SG_CACHE.clear()
        # un resultado de vigencia corta caduca pronto también en memoria, para volver a pedir la versión buena
        _SG_CACHE[key] = (time.time() - (21600 - min(ttl, 21600)) if ttl else time.time(), data)
        _SG_INFLIGHT.pop(key, None)
    if data:
        _geo_db_put("soilgrids_cache", f"{_SG_DBKEY}|{key[0]}|{key[1]}", data, ttl=ttl)
        print(f"[Suelo] guardado para los próximos análisis de esta zona ({time.time() - t0:.0f} s en total)")
    else:
        print("[Suelo] ningún proveedor dio datos para este punto; el análisis sigue sin datos satelitales")
    slot["data"] = data
    slot["ready"].set()
    slot["full"].set()
    slot["event"].set()


def fetch_soilgrids(lat: float, lng: float, wait=None, quiet=False):
    """
    Datos modelados (OpenLandMap-soildb a 30 m u, en su defecto, SoilGrids) para la celda de (lat, lng): de la memoria, de la base de
    datos o consultando con espera acotada (_SG_FG_WAIT). Una sola consulta por celda aunque lleguen peticiones simultáneas.
    None = sin dato por ahora; la consulta sigue en segundo plano y queda guardada. wait=0 solo la arranca (precarga).
    """
    gi, gj = round(lat / _SG_GRID), round(lng / _SG_GRID)
    key = (gi, gj)
    qlat, qlng = round(gi * _SG_GRID, 6), round(gj * _SG_GRID, 6)       # centro de la celda
    with _SG_LOCK:
        hit = _SG_CACHE.get(key)
        if hit and time.time() - hit[0] < (21600 if hit[1] else 120):
            return hit[1]
    saved = _geo_db_get("soilgrids_cache", f"{_SG_DBKEY}|{gi}|{gj}")
    if isinstance(saved, dict) and saved:
        with _SG_LOCK:
            _SG_CACHE[key] = (time.time(), saved)
        return saved
    _sg_health_load()
    with _SG_LOCK:
        slot = _SG_INFLIGHT.get(key)
        if slot is None:
            if _sg_all_down():
                return None
            slot = {"ready": threading.Event(), "full": threading.Event(), "event": threading.Event(), "data": None}
            _SG_INFLIGHT[key] = slot
            threading.Thread(target=_sg_fetch_bg, args=(key, qlat, qlng, slot), daemon=True).start()
    if wait is None:
        wait = _SG_FG_WAIT
    if not slot["ready"].wait(wait):
        if not quiet:
            print(f"[Suelo] los proveedores aún no responden tras {wait:.0f} s: el análisis sigue sin los datos satelitales. "
                  f"La consulta continúa en segundo plano y quedará guardada para la próxima vez en esta zona.")
        return None
    if not slot["full"].is_set() and wait > 0:
        slot["full"].wait(_SG_UPGRADE_WAIT)            # datos básicos ya listos: un poco más por la versión completa
    return slot["data"]


# ══════════════════════════════════════════════════════════════════════════════
#  CLIMA Y ALTITUD REALES DEL SITIO  (v3.9)
#
#  Antes la IA solo recibía el GPS y «adivinaba» el bioma. Ahora recibe datos medidos/modelados de fuentes gratuitas y sin clave:
#   · NASA POWER (power.larc.nasa.gov, climatología de largo plazo): temperatura media, máxima y mínima, lluvia, humedad relativa
#     y radiación solar de cada mes. Es una malla de ~50 km: las temperaturas se ajustan a la altitud real del sitio con el
#     gradiente estándar de 6.5 °C por km.
#   · Open-Meteo Elevation (modelo digital Copernicus DEM, 90 m): altitud del punto. Si no responde, se usa la de la malla de NASA.
#   · Clasificación de Köppen-Geiger calculada aquí mismo (método de Peel et al. 2007) con esas normales mensuales.
#  Todo se guarda 30 días (memoria + base de datos) y, si algo falla, el análisis sigue como antes (solo con el GPS).
# ══════════════════════════════════════════════════════════════════════════════
_POWER_URL = "https://power.larc.nasa.gov/api/temporal/climatology/point"
_POWER_PARAMS = ("T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR", "RH2M", "ALLSKY_SFC_SW_DWN")
_ELEV_URL = "https://api.open-meteo.com/v1/elevation"
_CLIM_TIMEOUT = _envf("CLIMATE_TIMEOUT", 10)
_CLIM_WAIT = _envf("CLIMATE_WAIT", 9)                    # el análisis espera como máximo estos segundos por el clima
_CLIM_UA = {"User-Agent": "EcoScan/3.9 (aplicación educativa de suelo y plantas)"}
_MON_KEYS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_MON_ES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
_MON_DAYS = (31, 28.25, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
_CLIM_MEM = {}            # clave → (instante, datos)
_CLIM_BAD = {}            # clave → hasta cuándo no se reintenta tras un fallo
_LAPSE_C_PER_M = 0.0065   # gradiente térmico estándar

_KOPPEN_ES = {
    "Af": "tropical húmedo (selva, sin estación seca)", "Am": "tropical monzónico (corta estación seca)",
    "Aw": "tropical de sabana (invierno seco)", "As": "tropical con verano seco",
    "BWh": "desértico cálido", "BWk": "desértico frío", "BSh": "semiárido cálido (estepa)", "BSk": "semiárido frío (estepa)",
    "Csa": "mediterráneo de verano cálido y seco", "Csb": "mediterráneo de verano templado y seco", "Csc": "mediterráneo fresco",
    "Cwa": "subtropical húmedo con invierno seco", "Cwb": "templado de altura con invierno seco", "Cwc": "subpolar de altura con invierno seco",
    "Cfa": "subtropical húmedo (lluvia todo el año)", "Cfb": "oceánico templado (lluvia todo el año)", "Cfc": "oceánico subpolar",
    "Dsa": "continental con verano cálido y seco", "Dsb": "continental con verano templado y seco", "Dsc": "subártico con verano seco", "Dsd": "subártico extremo con verano seco",
    "Dwa": "continental con verano cálido e invierno seco", "Dwb": "continental con verano templado e invierno seco", "Dwc": "subártico con invierno seco", "Dwd": "subártico extremo con invierno seco",
    "Dfa": "continental húmedo de verano cálido", "Dfb": "continental húmedo de verano templado", "Dfc": "subártico", "Dfd": "subártico extremo",
    "ET": "tundra", "EF": "hielo permanente",
}


def koppen_class(T, P, lat):
    """Köppen-Geiger (Peel et al. 2007). T: 12 temperaturas medias (°C); P: 12 lluvias (mm/mes); lat: hemisferio. → código (ej. 'Cwb')."""
    mat, map_ = sum(T) / 12.0, sum(P)
    tmax, tmin = max(T), min(T)
    summer = (3, 4, 5, 6, 7, 8) if lat >= 0 else (9, 10, 11, 0, 1, 2)
    winter = tuple(i for i in range(12) if i not in summer)
    psum, pwin = sum(P[i] for i in summer), sum(P[i] for i in winter)
    pdry = min(P)
    psdry, pwdry = min(P[i] for i in summer), min(P[i] for i in winter)
    pswet, pwwet = max(P[i] for i in summer), max(P[i] for i in winter)
    if map_ > 0 and pwin >= 0.7 * map_:
        pth = 2 * mat
    elif map_ > 0 and psum >= 0.7 * map_:
        pth = 2 * mat + 28
    else:
        pth = 2 * mat + 14
    if tmax < 10:
        return "EF" if tmax < 0 else "ET"
    if map_ < 10 * pth:
        return ("BW" if map_ < 5 * pth else "BS") + ("h" if mat >= 18 else "k")
    if tmin >= 18:
        if pdry >= 60:
            return "Af"
        return "Am" if pdry >= 100 - map_ / 25.0 else "Aw"
    grp = "C" if tmin > 0 else "D"
    if psdry < 40 and psdry < pwwet / 3.0:
        sec = "s"
    elif pwdry < pswet / 10.0:
        sec = "w"
    else:
        sec = "f"
    hot_months = sum(1 for t in T if t >= 10)
    if tmax >= 22:
        thr = "a"
    elif hot_months >= 4:
        thr = "b"
    elif tmin > -38:
        thr = "c"
    else:
        thr = "d"
    return grp + sec + thr


def _month_ranges(idx):
    """[0, 1, 2, 10, 11] → 'nov–mar' (une meses seguidos, también a través del cambio de año)."""
    idx = sorted(set(idx))
    if not idx:
        return ""
    if len(idx) == 12:
        return "todo el año"
    start = next((i for i in idx if (i - 1) % 12 not in idx), idx[0])    # primer mes de una racha
    runs, cur, i = [], [], start
    for _ in range(12):
        if i in idx:
            cur.append(i)
        elif cur:
            runs.append(cur)
            cur = []
        i = (i + 1) % 12
    if cur:
        runs.append(cur)
    return ", ".join(_MON_ES[r[0]] if len(r) == 1 else f"{_MON_ES[r[0]]}–{_MON_ES[r[-1]]}" for r in runs)


def _power_series(par: dict, name: str, lo: float, hi: float):
    v = par.get(name) or {}
    out = []
    for m in _MON_KEYS:
        x = v.get(m)
        out.append(float(x) if isinstance(x, (int, float)) and x != -999 and lo <= x <= hi else None)
    return out


def _power_fetch(lat: float, lng: float) -> dict:
    """Normales mensuales de NASA POWER para el punto. Lanza excepción si no responde o la respuesta no sirve."""
    resp = _req.get(_POWER_URL, headers=_CLIM_UA, timeout=(4, _CLIM_TIMEOUT), params={
        "parameters": ",".join(_POWER_PARAMS), "community": "AG", "longitude": round(lng, 4), "latitude": round(lat, 4), "format": "JSON"})
    if resp.status_code != 200:
        raise _SoilProviderError(f"NASA POWER: HTTP {resp.status_code}")
    j = resp.json()
    par = (j.get("properties") or {}).get("parameter") or {}
    t = _power_series(par, "T2M", -80, 60)
    pr = _power_series(par, "PRECTOTCORR", 0, 200)                      # mm/día
    if any(x is None for x in t) or any(x is None for x in pr):
        raise _SoilProviderError("NASA POWER: normales mensuales incompletas")
    coords = (j.get("geometry") or {}).get("coordinates") or []
    grid_elev = float(coords[2]) if len(coords) > 2 and isinstance(coords[2], (int, float)) else None
    return {"t": t, "tmax": _power_series(par, "T2M_MAX", -80, 70), "tmin": _power_series(par, "T2M_MIN", -90, 60),
            "p": [round(d * n, 1) for d, n in zip(pr, _MON_DAYS)], "rh": _power_series(par, "RH2M", 0, 100),
            "sw": _power_series(par, "ALLSKY_SFC_SW_DWN", 0, 15), "grid_elev": grid_elev}


def _elevation_fetch(lat: float, lng: float):
    """Altitud (m) del punto con el modelo Copernicus DEM (Open-Meteo). None si no responde."""
    try:
        resp = _req.get(_ELEV_URL, headers=_CLIM_UA, timeout=(3, 6), params={"latitude": round(lat, 5), "longitude": round(lng, 5)})
        if resp.status_code == 200:
            v = (resp.json().get("elevation") or [None])[0]
            if isinstance(v, (int, float)) and -450 <= v <= 9000:
                return float(v)
    except Exception as ex:
        print(f"[Clima] altitud: {type(ex).__name__}: {str(ex)[:100]}")
    return None


def _climate_build(raw: dict, elev, lat: float) -> dict:
    """Une normales + altitud: ajusta temperaturas a la altitud del sitio, clasifica (Köppen) y resume."""
    site = elev if elev is not None else raw.get("grid_elev")
    dz = 0.0
    if elev is not None and raw.get("grid_elev") is not None:
        dz = max(-1200.0, min(1200.0, elev - raw["grid_elev"]))
    adj = abs(dz) >= 100
    shift = -_LAPSE_C_PER_M * dz if adj else 0.0
    def f(arr):
        return [None if x is None else round(x + shift, 1) for x in arr]
    t, tmax, tmin = f(raw["t"]), f(raw["tmax"]), f(raw["tmin"])
    p = raw["p"]
    code = koppen_class(t, p, lat)
    mat, map_ = sum(t) / 12.0, sum(p)
    mean_p = map_ / 12.0
    wet = [i for i, x in enumerate(p) if x >= max(40.0, 1.5 * mean_p)]
    dry = [i for i, x in enumerate(p) if x < max(15.0, 0.35 * mean_p)] if map_ >= 150 else []
    tmin_vals = [x for x in tmin if x is not None]
    tmin_cold = min(tmin_vals) if tmin_vals else None
    frost = None
    if tmin_cold is not None:
        frost = ("heladas frecuentes" if tmin_cold <= 0 else "heladas ocasionales posibles" if tmin_cold <= 4 else "sin heladas esperables")
    sw = [x for x in raw["sw"] if x is not None]
    rh = [x for x in raw["rh"] if x is not None]
    return {
        "source": "NASA POWER (normales climáticas, malla ≈ 50 km)" + (" + Copernicus DEM vía Open-Meteo (altitud)" if elev is not None else ""),
        "elevation_m": round(site) if site is not None else None, "elevation_source": "dem" if elev is not None else "malla",
        "tempAdjusted": adj, "koppen": code, "koppenName": _KOPPEN_ES.get(code, ""),
        "mat_c": round(mat, 1), "map_mm": round(map_), "t_cold_c": round(min(t), 1), "t_hot_c": round(max(t), 1),
        "tmin_cold_c": round(tmin_cold, 1) if tmin_cold is not None else None, "frost": frost,
        "wet_months": _month_ranges(wet), "dry_months": _month_ranges(dry),
        "wettest": [_MON_ES[max(range(12), key=lambda i: p[i])], round(max(p))],
        "sw_kwh_m2_day": round(sum(sw) / len(sw), 1) if sw else None, "rh_pct": round(sum(rh) / len(rh)) if rh else None,
        "months": {"t": t, "p": [round(x) for x in p]},
    }


def fetch_climate(lat: float, lng: float, wait=None):
    """Clima + altitud del sitio (dict) o None si no hay datos a tiempo. Con caché de memoria y de base de datos (30 días)."""
    pk = f"{round(lat * 4)}|{round(lng * 4)}"          # NASA POWER es una malla gruesa: ~0.25° comparten consulta
    ek = f"{round(lat / 0.002)}|{round(lng / 0.002)}"  # la altitud sí es del punto (~220 m)
    ck = (pk, ek)
    with _SG_LOCK:
        hit = _CLIM_MEM.get(ck)
        if hit and time.time() - hit[0] < 21600:
            return hit[1]
        if time.time() < _CLIM_BAD.get(pk, 0):
            return None
    raw = _geo_db_get("soilgrids_cache", f"clim1|{pk}")
    elev = _geo_db_get("soilgrids_cache", f"elev1|{ek}")

    def get_raw():
        r = _power_fetch(lat, lng)
        _geo_db_put("soilgrids_cache", f"clim1|{pk}", r)
        return r

    def get_elev():
        e = _elevation_fetch(lat, lng)
        if e is not None:
            _geo_db_put("soilgrids_cache", f"elev1|{ek}", {"m": e})
        return e

    futs = {}
    pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="clima")
    try:
        if not (isinstance(raw, dict) and raw.get("t")):
            raw = None
            futs["raw"] = pool.submit(get_raw)
        if isinstance(elev, dict) and isinstance(elev.get("m"), (int, float)):
            elev = float(elev["m"])
        else:
            elev = None
            futs["elev"] = pool.submit(get_elev)
        limit = wait if wait is not None else _CLIM_WAIT
        t_end = time.time() + limit
        for name, fut in futs.items():
            try:
                val = fut.result(timeout=max(0.1, t_end - time.time()))
            except _FutureTimeout:
                print(f"[Clima] {'NASA POWER' if name == 'raw' else 'altitud'}: sin respuesta en {limit:.0f} s")
                continue
            except Exception as ex:
                print(f"[Clima] {'NASA POWER' if name == 'raw' else 'altitud'}: {type(ex).__name__}: {str(ex)[:120]}")
                continue
            if name == "raw":
                raw = val
            else:
                elev = val
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    if not raw:
        with _SG_LOCK:
            _CLIM_BAD[pk] = time.time() + 300              # 5 min sin reintentar si NASA POWER no responde
        return None
    try:
        out = _climate_build(raw, elev, lat)
    except Exception as ex:
        print(f"[Clima] no se pudo armar el resumen: {type(ex).__name__}: {ex}")
        return None
    with _SG_LOCK:
        if len(_CLIM_MEM) > 500:
            _CLIM_MEM.clear()
        _CLIM_MEM[ck] = (time.time(), out)
    return out


def build_climate_context(c: dict) -> str:
    """Texto para el prompt: el clima REAL del sitio, para que la IA no adivine el bioma a partir de un par de coordenadas."""
    if not c:
        return ""
    lines = [f"CLIMA REAL DEL SITIO ({c.get('source')}; son normales de largo plazo, no el tiempo de hoy):"]
    head = []
    if c.get("elevation_m") is not None:
        head.append(f"altitud {c['elevation_m']} m" + ("" if c.get("elevation_source") == "dem" else " (de la malla de NASA, aproximada)"))
    if c.get("koppen"):
        head.append(f"clima Köppen-Geiger {c['koppen']}" + (f" ({c['koppenName']})" if c.get("koppenName") else ""))
    if head:
        lines.append("  " + " · ".join(head))
    lines.append(f"  Temperatura media anual {c['mat_c']} °C (mes más frío {c['t_cold_c']} °C; más cálido {c['t_hot_c']} °C)"
                 + (f" · mínima media del mes más frío {c['tmin_cold_c']} °C → {c['frost']}" if c.get("tmin_cold_c") is not None else "")
                 + (" · temperaturas ajustadas a la altitud del sitio" if c.get("tempAdjusted") else ""))
    rain = f"  Lluvia anual {c['map_mm']} mm · mes más lluvioso: {c['wettest'][0]} ({c['wettest'][1]} mm)"
    if c.get("wet_months"):
        rain += f" · temporada de lluvias: {c['wet_months']}"
    if c.get("dry_months"):
        rain += f" · meses secos: {c['dry_months']}"
    lines.append(rain)
    extra = []
    if c.get("sw_kwh_m2_day") is not None:
        extra.append(f"radiación solar media {c['sw_kwh_m2_day']} kWh/m²/día")
    if c.get("rh_pct") is not None:
        extra.append(f"humedad relativa media {c['rh_pct']} %")
    if extra:
        lines.append("  " + " · ".join(extra))
    return "\n".join(lines)


def _texture_class(clay: float, sand: float, silt: float) -> str:
    """Clase textural USDA (triángulo completo) en español."""
    t = usda_texture(clay, sand, silt)
    return t["es"] if t else "franco"


_GROQ_TEXT_MODELS = [
    "openai/gpt-oss-120b",   # Producción · mejor calidad de análisis · 500 t/s
    "openai/gpt-oss-20b",    # Producción · más rápido · 1000 t/s
    "qwen/qwen3.8-27b",      # Respaldo adicional (motor distinto de OpenAI-OSS)
]

# Mensaje único para cualquier fallo de conexión con la IA (el cliente usa el mismo texto)
AI_CONN_ERROR = "Fallo de conexión con la IA. Verifica tu conexión a internet e inténtalo de nuevo."

# Modelos que rechazaron reasoning_effort="none" (se recuerda para no repetir el intento)
_NO_NONE_EFFORT = set()


def _reasoning_kwargs(model_id: str, json_mode: bool, effort: str = "low") -> dict:
    """
    Cada familia de modelo maneja el razonamiento con parámetros distintos.
    Esta función arma los kwargs correctos para que:
      - el razonamiento interno del modelo NUNCA aparezca mezclado en message.content
      - el nivel de esfuerzo se pueda ajustar según la tarea (diagnóstico completo vs chat rápido)
    """
    kw = {}
    if model_id.startswith("openai/gpt-oss"):
        # gpt-oss solo acepta low/medium/high; no soporta reasoning_format
        mapped = effort if effort in ("low", "medium", "high") else ("low" if effort == "none" else "medium")
        kw["reasoning_effort"] = mapped
        kw["include_reasoning"] = False   # no necesitamos exponer el razonamiento
    elif model_id.startswith("qwen/"):
        # Según la documentación de Groq, qwen3.8-27b admite none/default/low/medium/high.
        # "none" desactiva el razonamiento: no gasta tokens de salida pensando (se usa como plan B ante cortes).
        mapped = effort if effort in ("none", "low", "medium", "high") else "low"
        if mapped == "none" and model_id in _NO_NONE_EFFORT:
            mapped = "low"
        kw["reasoning_effort"] = mapped
        # con JSON mode "raw" no está permitido (daría error 400); "parsed" también evita que el chat muestre <think>
        kw["reasoning_format"] = "parsed"
    return kw


# ══════════════════════════════════════════════════════════════════════════════
#  LÍMITES DE GROQ
#  Algunas cuentas (nivel on_demand) tienen un tope de TOKENS DE SALIDA POR MINUTO (OTPM) muy bajo para qwen
#  (p. ej. 1000). Si una llamada pide más, Groq la rechaza con 429 «Request too large … OTPM: Limit 1000,
#  Requested 1134». Ese error NO se arregla esperando: hay que pedir menos. Por eso aquí:
#   · se lee «Limit/Requested» del propio error, se recalcula max_completion_tokens y se reintenta al instante;
#   · el tope aprendido se recuerda 1 h por modelo (si subes de plan, se vuelve a probar el valor completo);
#   · un 429 «de verdad» (Used + Requested > Limit) sí espera lo que Groq indica en «try again in Xs»;
#   · los modelos que no pueden entregar la salida mínima necesaria se saltan (una respuesta recortada = JSON roto).
# ══════════════════════════════════════════════════════════════════════════════
_OTPM_CAP = {}                 # (modelo, tarea) → (tope de max_completion_tokens aprendido, instante)
_OTPM_CAP_TTL = 3600
_LIMIT_RE = re.compile(r"Limit\s+(\d+)\s*,\s*(?:Used\s+(\d+)\s*,\s*)?Requested\s+(\d+)", re.I)


class _BudgetTooSmall(Exception):
    """El tope de salida de la cuenta es menor que lo mínimo que necesita esta tarea (se trata como límite de velocidad)."""


def _status_code(err):
    return getattr(err, "status_code", None)


def _is_rate_limit(err):
    s = str(err).lower()
    return _status_code(err) in (413, 429) or any(
        k in s for k in ["rate_limit", "rate limit", "429", "413", "too large", "tokens per minute", "quota"])


def _is_too_large(err):
    """La solicitud por sí sola excede el límite: esperar no sirve, hay que pedir menos."""
    s = str(err).lower()
    if "request too large" in s or "reduce max_tokens" in s or _status_code(err) == 413:
        return True
    m = _LIMIT_RE.search(str(err))
    return bool(m and int(m.group(3)) > int(m.group(1)))


def _retry_after(err, default=10.0):
    """Segundos que Groq pide esperar ('try again in 7.4s', '1m3.2s' o '450ms'); `default` si no los indica."""
    m = re.search(r"try again in\s+(?:(\d+)m(?!s))?\s*([\d.]+)\s*(ms|s)\b", str(err), re.I)
    if m:
        secs = float(m.group(2)) / (1000.0 if m.group(3).lower() == "ms" else 1.0)
        return int(m.group(1) or 0) * 60 + secs
    try:                                           # a veces solo viene en la cabecera retry-after
        h = err.response.headers.get("retry-after")
        if h:
            return float(h)
    except Exception:
        pass
    return default


# ── JSON inválido de la IA ────────────────────────────────────────────────────
# Con response_format=json_object Groq valida la salida y, si el modelo la estropea (p. ej. olvida el «[» de una lista),
# responde 400 «json_validate_failed» y adjunta en el propio error el texto generado («failed_generation»).
# En vez de descartar una respuesta casi perfecta, se intenta repararla; si no se puede, se repite con temperatura baja.
def _is_json_invalid(err):
    return _status_code(err) == 400 and "json_validate_failed" in str(err)


def _failed_generation(err):
    body = getattr(err, "body", None)
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict) and inner.get("failed_generation"):
            return str(inner["failed_generation"])
    return None


# «"clave":"a","b","c"]»  →  «"clave":["a","b","c"]»  (falta el corchete que abre la lista; un texto seguido de «:» es otra clave)
_MISSING_BRACKET_RE = re.compile(r'(:\s*)("(?:[^"\\]|\\.)*"(?:\s*,\s*"(?:[^"\\]|\\.)*"(?!\s*:))+\s*\])')
_TRAILING_COMMA_RE = re.compile(r',\s*([}\]])')


def _repair_json(text):
    """Objeto (dict) si `text` es JSON válido o se arregla con errores típicos de formato; None si no. Siempre se verifica con json.loads."""
    if not text:
        return None
    base = clean_json(text)
    attempts = [base]
    fixed = _MISSING_BRACKET_RE.sub(r"\1[\2", base)
    attempts += [fixed, _TRAILING_COMMA_RE.sub(r"\1", fixed)]
    for cand in attempts:
        try:
            obj = json.loads(cand, strict=False)             # strict=False: acepta saltos de línea dentro de un texto
        except (ValueError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


class _FakeChoice:
    def __init__(self, content):
        self.message = type("Msg", (), {"content": content})()
        self.finish_reason = "stop"


class _FakeCompletion:
    """Respuesta con la misma forma que la del cliente de Groq, construida con un JSON reparado."""
    def __init__(self, content):
        self.choices = [_FakeChoice(content)]


def _out_cap(model, tag="Groq"):
    # El tope se aprende por tarea (plantas, suelo, texto…): Groq calcula lo «pedido» también a partir del contenido de la
    # solicitud, así que lo que cabe en el escáner de suelo no es necesariamente lo que cabe en el de plantas.
    hit = _OTPM_CAP.get((model, tag))
    return hit[0] if hit and time.time() - hit[1] < _OTPM_CAP_TTL else None


def _shrink_budget(model, sent, err, tag="Groq"):
    """max_completion_tokens que sí cabe, calculado con las cifras del error («Limit 1000, Requested 1134»)."""
    m = _LIMIT_RE.search(str(err))
    if not m:
        return None
    limit, req = int(m.group(1)), int(m.group(3))
    if req <= limit:
        return None
    low = str(err).lower()
    if "otpm" in low or "output tokens" in low:
        # tope de SALIDA: lo pedido crece con max_completion_tokens, así que se escala con margen del 8 %
        new = int(sent * limit / req * 0.92)
        if new >= 128:
            _OTPM_CAP[(model, tag)] = (new, time.time())
    else:
        # tope total (entrada + salida) de esta solicitud: se recorta solo lo que sobra
        new = sent - (req - limit) - 64
    return new if new > 0 else None


def _groq_request(model, messages, max_tokens, *, temperature, top_p=0.95, json_mode=True, effort="low",
                  min_tokens=0, tag="Groq", max_wait=0.0):
    """
    Una llamada a Groq con el manejo completo de límites. Devuelve (respuesta, max_tokens usado) o lanza el error.
      min_tokens → si el tope de la cuenta es menor, lanza _BudgetTooSmall (esa tarea no cabe en este modelo)
      max_wait   → segundos máximos que se esperan ante un 429 real (0 = no esperar; pasar al siguiente modelo)
    """
    cap = _out_cap(model, tag)
    budget = min(max_tokens, cap) if cap else max_tokens
    waited = False
    bad_json_retry = False
    for _ in range(5):
        if budget < min_tokens:
            raise _BudgetTooSmall(f"rate_limit: {model} solo admite ~{budget} tokens de salida por solicitud en esta cuenta "
                                  f"y esta tarea necesita al menos {min_tokens}")
        kwargs = dict(model=model, messages=messages, temperature=temperature,
                      max_completion_tokens=budget, top_p=top_p)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        kwargs.update(_reasoning_kwargs(model, json_mode, effort))
        try:
            return client.chat.completions.create(**kwargs), budget
        except Exception as e:
            if _is_json_invalid(e):
                fixed = _repair_json(_failed_generation(e))
                if fixed is not None:
                    print(f"[{tag}] {model}: la IA devolvió JSON con un error de formato; se reparó automáticamente")
                    return _FakeCompletion(json.dumps(fixed, ensure_ascii=False)), budget
                if not bad_json_retry:
                    bad_json_retry = True
                    temperature = min(temperature, 0.15)
                    print(f"[{tag}] {model}: JSON inválido y sin reparación posible; se repite con temperatura baja")
                    continue
                raise
            if kwargs.get("reasoning_effort") == "none" and _status_code(e) == 400 and "reasoning" in str(e).lower():
                _NO_NONE_EFFORT.add(model)
                print(f"[{tag}] {model} no acepta reasoning_effort=none; se usa 'low'")
                effort = "low"
                continue
            if _is_too_large(e):
                new = _shrink_budget(model, budget, e, tag)
                if new and new < budget:
                    lm = _LIMIT_RE.search(str(e))
                    print(f"[{tag}] {model}: tu cuenta de Groq limita los tokens de salida "
                          f"(límite {lm.group(1)}, pedido {lm.group(3)}); max_tokens {budget} → {new} y se reintenta sin esperar")
                    budget = new
                    continue
                raise
            if _is_rate_limit(e) and not waited:
                wait = _retry_after(e)
                if wait <= max_wait:
                    waited = True
                    print(f"[{tag}] {model}: límite de velocidad, reintento en {wait:.1f}s")
                    time.sleep(wait + 0.5)
                    continue
            raise
    raise RuntimeError(f"{model}: demasiados reintentos con Groq")


def call_groq(prompt: str, max_tokens: int = 4000, temperature: float = 0.6,
              json_mode: bool = True, effort: str = "low", models=None, min_tokens=None):
    """
    Texto con la IA de Groq. Prueba los modelos en orden; ante un límite de velocidad pasa de inmediato al siguiente
    (cada modelo tiene su propia cuota) y, si todos chocaron, espera lo que Groq indique y da una segunda vuelta.
    min_tokens: salida mínima que necesita la tarea; un modelo con un tope de salida menor se salta (default 60 %).
    """
    if min_tokens is None:
        min_tokens = int(max_tokens * 0.6)
    messages = [{"role": "user", "content": prompt}]
    last_err = None
    for attempt in range(2):            # 2.ª vuelta: espera breve si todos los modelos chocaron con el límite de velocidad
        waits = []
        for model in (models or _GROQ_TEXT_MODELS):
            try:
                r, _ = _groq_request(model, messages, max_tokens, temperature=temperature, top_p=0.95,
                                     json_mode=json_mode, effort=effort, min_tokens=min_tokens, tag="Groq")
                choice = r.choices[0]
                if json_mode and choice.finish_reason == "length":
                    # respuesta cortada a la mitad: el JSON no sería válido, se prueba con el siguiente modelo
                    print(f"[Groq] {model}: la respuesta se cortó por límite de tokens, probando siguiente...")
                    last_err = RuntimeError("respuesta truncada")
                    continue
                return choice.message.content
            except Exception as e:
                last_err = e
                if _is_rate_limit(e) and not _is_too_large(e) and not isinstance(e, _BudgetTooSmall):
                    waits.append(_retry_after(e))
                err_str = str(e).lower()
                if _is_json_invalid(e) or any(k in err_str for k in ["model", "rate", "quota", "limit", "decommission",
                                                                    "not found", "does not exist", "deprecat"]):
                    print(f"[Groq] Modelo {model} no disponible, probando siguiente... ({e})")
                    continue
                print(f"[Groq] Error irrecuperable: {e}")
                return None
        if attempt == 0 and waits and min(waits) <= 15:
            print(f"[Groq] Todos los modelos con límite de velocidad: reintento en {min(waits):.1f}s")
            time.sleep(min(waits) + 0.5)
            continue
        break
    print(f"[Groq] Todos los modelos fallaron. Último error: {last_err}")
    return None


def clean_json(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```json"):
        s = s[7:]
    if s.startswith("```"):
        s = "\n".join(s.splitlines()[1:])
    if s.endswith("```"):
        s = "\n".join(s.splitlines()[:-1])
    return s.strip()


# ── Respuestas de la IA: extraer el JSON y garantizar su forma ────────────────────────────────────────────────
# Dos fallos hacían que el informe saliera en blanco o con error aunque la IA sí respondiera: (1) un JSON casi válido
# (texto antes o después, coma final, respuesta cortada) que json.loads rechazaba, y (2) un JSON válido pero con otra
# forma (un texto donde la página espera una lista, un número como «72 %», un campo nulo) que rompía al pintarlo.
def _balance_json(s):
    """Desde el primer «{», el objeto completo; si el texto se cortó, lo cierra (descarta la clave o el texto a medias)."""
    start = s.find("{")
    if start < 0:
        return None
    stack, in_str, esc = [], False, False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and stack[-1] == ch:
                stack.pop()
            if not stack:
                return s[start:i + 1]
    out = s[start:].rstrip()
    if in_str:
        out += '"'
    for _ in range(3):
        out = re.sub(r"[\s,]+$", "", out)
        out = re.sub(r":\s*$", "", out)                                    # «"clave":» sin valor
        if stack and stack[-1] == "}":                                     # «,"clave"» suelta dentro de un objeto
            out = re.sub(r'(?<=[{,])\s*"(?:[^"\\]|\\.)*"\s*$', "", out)
    return out + "".join(reversed(stack))


def parse_ai_json(raw):
    """dict con la respuesta de la IA. Tolera ```json, texto antes o después, comas finales, el «[» olvidado de una lista
    y respuestas cortadas. Lanza json.JSONDecodeError si no hay forma de rescatarla."""
    if isinstance(raw, str) and raw.strip():
        obj = _repair_json(raw)
        if obj is None:
            cand = _balance_json(raw)
            if cand:
                fixed = _MISSING_BRACKET_RE.sub(r"\1[\2", cand)
                for c in (cand, fixed, _TRAILING_COMMA_RE.sub(r"\1", fixed)):
                    try:
                        o = json.loads(c, strict=False)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(o, dict):
                        obj = o
                        break
        if isinstance(obj, dict) and obj:
            return obj
    raise json.JSONDecodeError("la IA no devolvió un objeto JSON utilizable", (raw or "")[:60] if isinstance(raw, str) else "", 0)


def call_groq_json(prompt, tries=2, **kw):
    """call_groq + parse_ai_json. Si la respuesta no se puede leer, repite una vez con menos temperatura.
    None si la IA no está disponible; JSONDecodeError si dos respuestas seguidas fueron ilegibles."""
    last = None
    for i in range(max(1, tries)):
        k = dict(kw)
        if i:
            k["temperature"] = min(float(k.get("temperature", 0.5)), 0.25)
        raw = call_groq(prompt, **k)
        if not raw:
            return None
        try:
            return parse_ai_json(raw)
        except json.JSONDecodeError as e:
            last = e
            print(f"[Groq] respuesta ilegible ({e}); {'se repite' if i + 1 < tries else 'sin más intentos'}")
    raise last


def _txt(v, n=700):
    """Texto: acepta texto, número, lista o diccionario (se une) y nunca devuelve None."""
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, str):
        return v.strip()[:n]
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "; ".join(t for t in (_txt(x, n) for x in v) if t)[:n]
    if isinstance(v, dict):
        return " — ".join(t for t in (_txt(x, n) for x in v.values()) if t)[:n]
    return ""


_BULLET_SPLIT = re.compile(r"\s*(?:\r?\n+|;\s+|•|\s[-–]\s|(?:^|\s)\d{1,2}[.)]\s)\s*")


def _lst(v, n=12, m=400):
    """Lista de textos: acepta una lista, un texto con saltos de línea o viñetas, o un diccionario."""
    if v is None:
        return []
    if isinstance(v, str):
        v = [x for x in _BULLET_SPLIT.split(v.strip()) if x and x.strip()]
    elif isinstance(v, dict):
        v = list(v.values())
    elif not isinstance(v, (list, tuple)):
        return []
    return [t for t in (_txt(x, m) for x in v) if t][:n]


def _rows(v, keys, n=8, m=500, req="name"):
    """Lista de fichas {campo: texto}. Un texto suelto se toma como el nombre; se descartan las fichas sin nombre."""
    if isinstance(v, dict):
        v = [v] if req in v else list(v.values())
    if isinstance(v, str):
        parts = _lst(v, n, m)
        if len(parts) == 1 and "," in parts[0]:
            parts = [x.strip() for x in parts[0].split(",") if x.strip()][:n]        # «Maíz, frijol» → dos fichas
        v = parts
    if not isinstance(v, (list, tuple)):
        return []
    out = []
    for it in v:
        if isinstance(it, str):
            it = {req: it}
        if not isinstance(it, dict):
            continue
        row = {k: _txt(it.get(k), m) for k in keys}
        if row.get(req):
            out.append(row)
    return out[:n]


def _int(v, lo=0, hi=100, default=None):
    """Entero acotado a [lo, hi]; acepta 72, 72.4, «72 %» o «72/100»."""
    if isinstance(v, bool) or v is None:
        return default
    if isinstance(v, (int, float)):
        x = float(v)
    else:
        mm = re.search(r"-?\d+(?:[.,]\d+)?", str(v))
        if not mm:
            return default
        x = float(mm.group(0).replace(",", "."))
    if x != x:
        return default
    return int(round(max(lo, min(hi, x))))


def _pick(v, options, default="", aliases=None):
    """Valor dentro de un conjunto cerrado (sin importar mayúsculas ni tildes); `default` si no coincide."""
    def fold(t):
        return "".join(c for c in unicodedata.normalize("NFD", str(t or "").lower().strip()) if unicodedata.category(c) != "Mn")
    t = fold(_txt(v, 60))
    for o in options:
        if fold(o) == t:
            return o
    for a, o in (aliases or {}).items():
        if fold(a) == t:
            return o
    return default


def normalize_soil_result(r):
    """Forma garantizada del informe de suelo que devuelve la IA: textos son textos, listas son listas, la puntuación es
    un entero 0-100 y los niveles pertenecen a su conjunto cerrado. Lo que falta se omite (la página lo trata como «sin dato»)."""
    r = r if isinstance(r, dict) else {}
    out = {}
    ss = r.get("soilStatus") if isinstance(r.get("soilStatus"), dict) else {}
    sal = ss.get("salinity")
    sal = sal if isinstance(sal, dict) else ({"level": sal} if isinstance(sal, str) and len(sal) < 14 else {"impact": sal})
    nut = ss.get("nutrients") if isinstance(ss.get("nutrients"), dict) else {}
    out["soilStatus"] = {
        "phAnalysis": _txt(ss.get("phAnalysis")),
        "soilHealth": _txt(ss.get("soilHealth")),
        "mainIssues": _lst(ss.get("mainIssues"), 8),
        "salinity": {"level": _pick(sal.get("level"), ("baja", "media", "alta", "muy alta"),
                                    aliases={"bajo": "baja", "medio": "media", "alto": "alta", "muy alto": "muy alta"}),
                     "impact": _txt(sal.get("impact")), "recommendation": _txt(sal.get("recommendation"))},
        "nutrients": {
            "nitrogen": _pick(nut.get("nitrogen"), ("deficiente", "adecuado", "exceso", "sin dato"), "sin dato"),
            "phosphorus": _pick(nut.get("phosphorus"), ("deficiente", "adecuado", "exceso", "sin dato"), "sin dato"),
            "potassium": _pick(nut.get("potassium"), ("deficiente", "adecuado", "exceso", "sin dato"), "sin dato"),
            "organicMatter": _pick(nut.get("organicMatter"), ("bajo", "medio", "alto")),
            "analysis": _txt(nut.get("analysis"))},
        "measurementAdvice": _lst(ss.get("measurementAdvice"), 6),
    }
    pe = r.get("pests") if isinstance(r.get("pests"), dict) else {}
    if pe:
        out["pests"] = {
            "riskLevel": _pick(pe.get("riskLevel"), ("bajo", "medio", "alto", "crítico"), "medio",
                               aliases={"baja": "bajo", "media": "medio", "alta": "alto", "critico": "crítico", "critica": "crítico",
                                        "muy alto": "crítico", "muy alta": "crítico"}),
            "commonPests": _rows(pe.get("commonPests"), ("name", "type", "description", "control"), 6),
            "preventionTips": _lst(pe.get("preventionTips"), 8)}
    su = r.get("suitability") if isinstance(r.get("suitability"), dict) else {}
    lv_all = ("Óptimo", "Aceptable", "Requiere rehabilitación", "Crítico")
    lv = _pick(su.get("level"), lv_all, aliases={"optimo": "Óptimo", "requiere rehabilitacion": "Requiere rehabilitación",
                                                 "critico": "Crítico", "rehabilitacion": "Requiere rehabilitación"})
    sc = _int(su.get("score"))
    if sc is None and lv:
        sc = {"Óptimo": 85, "Aceptable": 65, "Requiere rehabilitación": 40, "Crítico": 20}[lv]
    if sc is not None:
        if not lv:
            lv = "Óptimo" if sc >= 80 else "Aceptable" if sc >= 60 else "Requiere rehabilitación" if sc >= 35 else "Crítico"
        out["suitability"] = {"level": lv, "score": sc, "message": _txt(su.get("message"))}
    re_ = r.get("rehabilitation") if isinstance(r.get("rehabilitation"), dict) else {}
    out["rehabilitation"] = {"immediateActions": _lst(re_.get("immediateActions"), 8),
                             "longTermActions": _lst(re_.get("longTermActions"), 8), "amendments": _lst(re_.get("amendments"), 8)}
    pl = r.get("plants") if isinstance(r.get("plants"), dict) else {}

    def water(rows):
        for x in rows:
            if "waterNeeds" in x:
                x["waterNeeds"] = _pick(x["waterNeeds"], ("bajo", "medio", "alto"), _txt(x["waterNeeds"], 40).lower(),
                                        aliases={"baja": "bajo", "media": "medio", "alta": "alto"})
        return rows
    out["plants"] = {
        "reforestation": water(_rows(pl.get("reforestation"), ("name", "reason", "care", "nativeRegion", "waterNeeds"), 8)),
        "agriculture": _rows(pl.get("agriculture"), ("name", "reason", "care", "season", "yield"), 8),
        "coverCrops": _rows(pl.get("coverCrops"), ("name", "benefit", "howToUse"), 8)}
    wm = r.get("waterManagement") if isinstance(r.get("waterManagement"), dict) else {}
    if wm:
        out["waterManagement"] = {"irrigationType": _txt(wm.get("irrigationType")), "frequency": _txt(wm.get("frequency")),
                                  "tips": _lst(wm.get("tips"), 8)}
    cr = r.get("careRoutine") if isinstance(r.get("careRoutine"), dict) else {}
    out["careRoutine"] = {"soilCare": _lst(cr.get("soilCare"), 8), "plantCare": _lst(cr.get("plantCare"), 8),
                          "schedule": _txt(cr.get("schedule"), 1200)}
    return out


def _soil_content(r):
    """Cuánto contenido útil trae un informe de suelo ya normalizado (especies + acciones + cuidados)."""
    return (sum(len(v) for v in r["plants"].values()) + len(r["rehabilitation"]["immediateActions"])
            + len(r["careRoutine"]["soilCare"]) + len(r["soilStatus"]["mainIssues"]))


_PLANT_HEALTH_STATES = ("sana", "estrés leve", "enferma", "crítica")


def normalize_plant_ident(d):
    """Identificación de la planta (foto o formulario) con forma garantizada."""
    d = d if isinstance(d, dict) else {}
    ip = d.get("isPlant")
    is_plant = ip if isinstance(ip, bool) else str(ip).strip().lower() in ("true", "sí", "si", "1", "yes")
    conf = d.get("confidence")
    if isinstance(conf, float) and 0 < conf <= 1:
        conf *= 100                                       # algunas respuestas dan la confianza como fracción
    out = {"isPlant": bool(is_plant), "confidence": _int(conf, default=0)}
    if not is_plant:
        out["rejection_reason"] = _txt(d.get("rejection_reason") or d.get("reason"), 300)
        return out
    for k in ("commonName", "scientificName", "family", "plantType", "lifeCycle", "origin", "habitat", "description", "potObserved"):
        out[k] = _txt(d.get(k), 400)
    if "isPotted" in d:
        ipot = d.get("isPotted")
        out["isPotted"] = ipot if isinstance(ipot, bool) else str(ipot).strip().lower() in ("true", "sí", "si", "1", "yes")
    out["quickCare"] = _lst(d.get("quickCare"), 6, 200)
    ob = d.get("observations")
    if isinstance(ob, dict):
        out["observations"] = {k: _txt(ob.get(k), 400) for k in ("overall", "leaves", "stemsAndBase", "substrate", "pestsSeen")}
        out["observations"]["spots"] = _rows(ob.get("spots"), ("area", "color", "shape", "pattern"), 6, 200, req="area")
    return out


def normalize_plant_health(d):
    d = d if isinstance(d, dict) else {}
    st = _pick(d.get("healthStatus"), _PLANT_HEALTH_STATES,
               aliases={"estres leve": "estrés leve", "critica": "crítica", "saludable": "sana", "sano": "sana", "enfermo": "enferma"})
    sc = _int(d.get("healthScore"))
    if not st:
        st = "sana" if (sc or 80) >= 80 else "estrés leve" if (sc or 80) >= 55 else "enferma" if (sc or 80) >= 30 else "crítica"
    if sc is None:
        sc = {"sana": 85, "estrés leve": 65, "enferma": 40, "crítica": 20}[st]
    raw = d.get("problems")
    if isinstance(raw, dict):
        raw = [raw] if "name" in raw else list(raw.values())
    if isinstance(raw, str):
        raw = _lst(raw, 4)
    probs = []
    for it in (raw if isinstance(raw, (list, tuple)) else []):
        if isinstance(it, str):
            it = {"name": it}
        name = _txt(it.get("name"), 200) if isinstance(it, dict) else ""
        if not name:
            continue
        probs.append({"name": name, "type": _txt(it.get("type"), 60).lower(), "probability": _int(it.get("probability"), default=50),
                      "severity": _pick(it.get("severity"), ("leve", "moderada", "grave"), "moderada",
                                        aliases={"moderado": "moderada", "media": "moderada", "medio": "moderada", "alta": "grave",
                                                 "alto": "grave", "severa": "grave", "severo": "grave", "baja": "leve", "bajo": "leve"}),
                      "evidence": _txt(it.get("evidence"), 500), "treatment": _lst(it.get("treatment"), 6, 300),
                      "prevention": _txt(it.get("prevention"), 400)})
        if len(probs) >= 4:
            break
    return {"healthStatus": st, "healthScore": sc, "summary": _txt(d.get("summary"), 600), "problems": probs,
            "urgentActions": _lst(d.get("urgentActions"), 6, 300)}


def normalize_plant_report(d):
    d = d if isinstance(d, dict) else {}
    cn = d.get("careNeeds") if isinstance(d.get("careNeeds"), dict) else {}
    pr = d.get("potRecommendation") if isinstance(d.get("potRecommendation"), dict) else {}
    cp = d.get("carePlan") if isinstance(d.get("carePlan"), dict) else {}
    return {
        "summary": _txt(d.get("summary"), 800),
        "careNeeds": {k: _txt(cn.get(k), 400) for k in ("light", "watering", "humidity", "temperature", "substrate", "fertilizing", "pruning")},
        "potRecommendation": {**{k: _txt(pr.get(k), 400) for k in ("material", "size", "depth", "drainage", "substrate", "repotting", "currentPotAssessment")},
                              "extraTips": _lst(pr.get("extraTips"), 6, 300)},
        "carePlan": {k: _lst(cp.get(k), 6, 300) for k in ("thisWeek", "thisMonth", "ongoing")},
        "warnings": _lst(d.get("warnings"), 4, 300),
    }


def build_db_context(matches: list) -> str:
    """Construye texto de contexto con los perfiles más similares."""
    if not matches:
        return ""
    lines = ["REFERENCIA DE BASE DE DATOS (perfiles de suelo más parecidos):"]
    for i, m in enumerate(matches[:2], 1):
        p = m["profile"]
        lines.append(
            f"\nPerfil #{i} — {p['name']} (Confianza BD: {m['score']}/100)"
            f"\n  Clase FAO: {p['fao_class']} | USDA: {p['usda_class']}"
            f"\n  pH típico: {p['ph_range'][0]}-{p['ph_range'][1]} | Textura: arcilla {p['clay_pct'][0]}-{p['clay_pct'][1]}%, arena {p['sand_pct'][0]}-{p['sand_pct'][1]}%"
            f"\n  Regiones típicas: {', '.join(p['regions'][:3])}"
            f"\n  Problemas comunes: {', '.join(p['issues'][:3])}"
            f"\n  Cultivos típicos: {', '.join(p['typical_crops'][:5])}"
            f"\n  Plantas nativas: {', '.join(p['native_plants'][:3])}"
            f"\n  Enmiendas recomendadas: {', '.join(p['amendments'][:3])}"
        )
    return "\n".join(lines)


def build_sg_context(sg: dict) -> str:
    """Contexto con las ESTIMACIONES MODELADAS por satélite (OpenLandMap-soildb o SoilGrids; no son mediciones de tu terreno)."""
    if not sg:
        return ""
    supp = sg.get("supplement") or {}
    parts = [f"ESTIMACIONES MODELADAS ({sg.get('source') or 'ISRIC SoilGrids 250 m'}, capa {sg.get('depth') or '0-5 cm'}; "
             f"no son mediciones de campo):"]
    if "ph_real" in sg:
        parts.append(f"  pH modelado: {sg['ph_real']}")
    if "clay_pct" in sg:
        parts.append(f"  Arcilla: {sg['clay_pct']}% | Arena: {sg.get('sand_pct','?')}% | Limo: {sg.get('silt_pct','?')}%")
    if "texture_class" in sg:
        parts.append(f"  Clase textural modelada: {sg['texture_class']}")
    if "organic_carbon_pct" in sg:
        parts.append(f"  Carbono orgánico: {sg['organic_carbon_pct']}% (M.O. ≈ {round(sg['organic_carbon_pct']*1.724, 2)}%)")
    if "bulk_density_g_cm3" in sg:
        parts.append(f"  Densidad aparente: {sg['bulk_density_g_cm3']} g/cm³")
    if "cec_cmolkg" in sg:
        parts.append(f"  CIC modelada: {sg['cec_cmolkg']} cmol/kg" + (f" ({supp['cec_cmolkg']})" if "cec_cmolkg" in supp else ""))
    if "nitrogen_g_kg" in sg:
        parts.append(f"  Nitrógeno total modelado: {sg['nitrogen_g_kg']} g/kg" + (f" ({supp['nitrogen_g_kg']})" if "nitrogen_g_kg" in supp else ""))
    return "\n".join(parts)


# ══════════════════════════════════════════════════════════════════════════════
#  PRECISIÓN Y PROCEDENCIA DE LOS DATOS  (v3.7)
#
#  Una foto NO permite medir pH, salinidad, N-P-K ni la textura exacta. Por eso aquí:
#   1. cada dato lleva su PROCEDENCIA (medido en laboratorio / en campo / perfil medido cercano /
#      modelado por satélite / declarado / estimado por IA) y su INCERTIDUMBRE;
#   2. solo se fusionan las fuentes del mejor nivel disponible (una medición directa nunca se "promedia"
#      con una estimación de la IA) y se avisa cuando dos fuentes se contradicen;
#   3. las CLASES (ácido, salino, bajo en fósforo…) las fija el servidor con umbrales de la NOM-021-SEMARNAT-2000,
#      no la IA, y lo que no se midió se declara "sin medición" en vez de inventar una cifra;
#   4. se busca el PERFIL MEDIDO MÁS CERCANO (tabla local + WoSIS de ISRIC) y se registra la calibración
#      de la IA contra mediciones reales (solo con el consentimiento del usuario).
# ══════════════════════════════════════════════════════════════════════════════
PROV_LABEL = {
    "lab":      "Medido en laboratorio",
    "field":    "Medido en campo",
    "test":     "Prueba rápida de campo",
    "nearby":   "Perfil medido cercano",
    "modeled":  "Modelado satelital (OpenLandMap-soildb / SoilGrids)",
    "declared": "Declarado por el usuario",
    "ai":       "Estimado por IA desde la foto",
    "ref":      "Referencia regional (BD)",
    "none":     "Sin medición",
}
# nivel de la fuente: 3 = medición directa · 2 = indicio medido o modelo · 1 = estimación / declaración
_TIER = {"lab": 3, "field": 3, "test": 2, "nearby": 2, "modeled": 2, "declared": 1, "ai": 1, "ref": 1}
# cuánto aporta cada procedencia al "puntaje de calidad de los datos"
_TIER_FACTOR = {"lab": 1.0, "field": 0.85, "test": 0.5, "nearby": 0.55, "modeled": 0.5, "declared": 0.35, "ai": 0.15, "ref": 0.1}

PROP_ORDER = ["ph", "ec", "om", "clay", "sand", "silt", "cec", "n", "p", "k"]
PROPS = {
    "ph":   dict(label="pH (en agua)",              unit="",        w=25, dec=1, lo=2.5, hi=11.0,   floor=1.0),
    "ec":   dict(label="Conductividad eléctrica",   unit="dS/m",    w=20, dec=2, lo=0.0, hi=80.0,   floor=1.0),
    "om":   dict(label="Materia orgánica",          unit="%",       w=15, dec=1, lo=0.0, hi=90.0,   floor=1.0),
    "clay": dict(label="Arcilla",                   unit="%",       w=5,  dec=0, lo=0.0, hi=100.0,  floor=10.0),
    "sand": dict(label="Arena",                     unit="%",       w=5,  dec=0, lo=0.0, hi=100.0,  floor=10.0),
    "silt": dict(label="Limo",                      unit="%",       w=5,  dec=0, lo=0.0, hi=100.0,  floor=10.0),
    "cec":  dict(label="Capacidad de intercambio catiónico (CIC)", unit="cmol/kg", w=10, dec=1, lo=0.0, hi=150.0, floor=5.0),
    "n":    dict(label="Nitrógeno inorgánico",      unit="mg/kg",   w=5,  dec=0, lo=0.0, hi=1000.0, floor=10.0),
    "p":    dict(label="Fósforo disponible",        unit="mg/kg",   w=5,  dec=1, lo=0.0, hi=1000.0, floor=5.0),
    "k":    dict(label="Potasio intercambiable",    unit="cmol/kg", w=5,  dec=2, lo=0.0, hi=20.0,   floor=0.2),
}


def _num(v, lo=None, hi=None):
    """float válido (y dentro de [lo, hi]) o None. Los datos llegan del cliente: nunca se confía en ellos."""
    try:
        if v is None or isinstance(v, bool):
            return None
        x = float(str(v).strip().replace(",", ".")) if isinstance(v, str) else float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        return None
    return x


# ── Clasificación textural USDA (triángulo completo, 12 clases) ───────────────
_USDA_ES = {
    "sand": "arenoso", "loamy sand": "arenoso franco", "sandy loam": "franco arenoso", "loam": "franco",
    "silt loam": "franco limoso", "silt": "limoso", "sandy clay loam": "franco arcillo arenoso",
    "clay loam": "franco arcilloso", "silty clay loam": "franco arcillo limoso", "sandy clay": "arcillo arenoso",
    "silty clay": "arcillo limoso", "clay": "arcilloso",
}
# clase USDA → tipo de suelo del formulario de EcoScan
_USDA_APPKEY = {
    "sand": "arenoso", "loamy sand": "arenoso", "sandy loam": "franco_arenoso", "loam": "franco",
    "silt loam": "limoso", "silt": "limoso", "sandy clay loam": "franco_arcilloso", "clay loam": "franco_arcilloso",
    "silty clay loam": "franco_arcilloso", "sandy clay": "arcilloso", "silty clay": "arcilloso", "clay": "arcilloso",
}


def usda_texture(clay, sand, silt):
    """Clase textural USDA a partir de % de arcilla, arena y limo (se normalizan a 100)."""
    tot = clay + sand + silt
    if tot <= 0:
        return None
    clay, sand, silt = clay * 100 / tot, sand * 100 / tot, silt * 100 / tot
    if silt + 1.5 * clay < 15:
        en = "sand"
    elif silt + 1.5 * clay >= 15 and silt + 2 * clay < 30:
        en = "loamy sand"
    elif (7 <= clay < 20 and sand > 52 and silt + 2 * clay >= 30) or (clay < 7 and silt < 50 and silt + 2 * clay >= 30):
        en = "sandy loam"
    elif 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        en = "loam"
    elif (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        en = "silt loam"
    elif silt >= 80 and clay < 12:
        en = "silt"
    elif 20 <= clay < 35 and silt < 28 and sand > 45:
        en = "sandy clay loam"
    elif 27 <= clay < 40 and 20 < sand <= 45:
        en = "clay loam"
    elif 27 <= clay < 40 and sand <= 20:
        en = "silty clay loam"
    elif clay >= 35 and sand > 45:
        en = "sandy clay"
    elif clay >= 40 and silt >= 40:
        en = "silty clay"
    elif clay >= 40 and sand <= 45 and silt < 40:
        en = "clay"
    else:
        en = "loam"
    return {"en": en, "es": _USDA_ES[en], "appKey": _USDA_APPKEY[en],
            "clay": round(clay), "sand": round(sand), "silt": round(silt)}


# centro aproximado de cada tipo de suelo del formulario (solo cuando NO hay nada mejor; sd grande)
_SOILTYPE_TEX = {
    "arenoso": (6, 84, 10), "franco_arenoso": (12, 64, 24), "franco": (20, 40, 40), "limoso": (12, 18, 70),
    "franco_arcilloso": (33, 30, 37), "arcilloso": (50, 20, 30),
}
# materia orgánica según la foto (centro de clase NOM-021 no volcánica ± incertidumbre amplia)
_OM_PHOTO = {"bajo": (1.0, 0.9), "medio": (2.5, 1.3), "alto": (4.8, 2.0)}
# prueba de la cinta (ribbon test) → arcilla %
_RIBBON = {"none": (5, 5, "no forma cinta"), "short": (18, 8, "cinta < 2.5 cm"),
           "medium": (33, 7, "cinta de 2.5 a 5 cm"), "long": (50, 10, "cinta > 5 cm")}
# factor para pasar CE 1:5 → CE del extracto de saturación según textura (Slavich y Petterson, 1993; aproximado)
_EC_FACTOR = {"sand": 17.0, "loamy sand": 14.0, "sandy loam": 13.8, "loam": 9.5, "silt loam": 9.5, "silt": 9.5,
              "sandy clay loam": 8.6, "clay loam": 8.6, "silty clay loam": 8.6, "sandy clay": 7.5, "silty clay": 5.8, "clay": 5.8}
_EC_FACTOR_KEY = {"arenoso": 17.0, "franco_arenoso": 13.8, "franco": 9.5, "limoso": 9.5, "franco_arcilloso": 8.6, "arcilloso": 5.8}

_PH_METHODS = {   # método → (corrección a pH en agua, incertidumbre, procedencia, descripción)
    "lab_water": (0.0, 0.15, "lab",      "laboratorio · agua 1:2.5"),
    "cacl2":     (0.6, 0.35, "lab",      "laboratorio · CaCl₂, convertido a agua (+0.6, aproximado)"),
    "kcl":       (0.7, 0.40, "lab",      "laboratorio · KCl, convertido a agua (+0.7, aproximado)"),
    "meter":     (0.0, 0.25, "field",    "potenciómetro calibrado"),
    "strip":     (0.0, 0.50, "field",    "tiras o kit colorimétrico"),
    "estimate":  (0.0, 1.00, "declared", "estimación del usuario, sin instrumento"),
}


# ── Clases interpretativas ────────────────────────────────────────────────────
def classify(prop, v, p_method="olsen"):
    """(clase, norma de referencia). pH, M.O., N inorgánico, P-Olsen y el umbral salino (≥ 4 dS/m) son de la
    NOM-021-SEMARNAT-2000; las demás clases son referencias agronómicas generales."""
    if v is None:
        return None, None
    if prop == "ph":
        c = ("fuertemente ácido" if v < 5.05 else "moderadamente ácido" if v < 6.55 else "neutro" if v < 7.35
             else "medianamente alcalino" if v < 8.55 else "fuertemente alcalino")
        return c, "NOM-021-SEMARNAT-2000"
    if prop == "ec":
        c = ("no salino" if v < 2 else "ligeramente salino" if v < 4 else "salino" if v < 8
             else "fuertemente salino" if v < 16 else "extremadamente salino")
        return c, "NOM-021 (suelo salino ≥ 4 dS/m) y escala USDA"
    if prop == "om":
        c = "muy bajo" if v <= 0.55 else "bajo" if v <= 1.55 else "medio" if v <= 3.55 else "alto" if v <= 6.05 else "muy alto"
        return c, "NOM-021 (suelos no volcánicos)"
    if prop == "n":
        c = "muy bajo" if v <= 10 else "bajo" if v <= 20 else "medio" if v <= 40 else "alto" if v <= 60 else "muy alto"
        return c, "NOM-021-SEMARNAT-2000"
    if prop == "p":
        if p_method == "bray":
            return ("bajo" if v < 15 else "medio" if v <= 30 else "alto"), "referencia general (P Bray)"
        return ("bajo" if v < 5.5 else "medio" if v <= 11 else "alto"), "NOM-021 (P Olsen)"
    if prop == "k":
        return ("bajo" if v < 0.3 else "medio" if v <= 0.6 else "alto"), "referencia agronómica general"
    if prop == "cec":
        c = "muy baja" if v < 5 else "baja" if v < 15 else "media" if v < 25 else "alta" if v < 40 else "muy alta"
        return c, "referencia agronómica general"
    return None, None


def _ec_level(v):
    """Nivel de salinidad con el vocabulario de la app (baja|media|alta|muy alta)."""
    return "baja" if v < 2 else "media" if v < 4 else "alta" if v < 8 else "muy alta"


def _om_level(v):
    c, _ = classify("om", v)
    return "bajo" if c in ("muy bajo", "bajo") else "medio" if c == "medio" else "alto"


def _nutrient_level(prop, v, p_method="olsen"):
    """Etiqueta del formulario de nutrientes (deficiente|adecuado|exceso) a partir de la clase."""
    c, _ = classify(prop, v, p_method)
    if c in ("muy bajo", "bajo"):
        return "deficiente"
    if prop == "n" and c == "muy alto":
        return "exceso"
    return "adecuado"


# ── Recolección de datos: cada valor entra con su procedencia e incertidumbre ──
def _add(E, prop, v, sd, src, note="", km=None):
    if v is None:
        return
    e = {"v": float(v), "sd": max(float(sd), 1e-6), "src": src, "note": note}
    if km is not None:
        e["km"] = round(km, 1)
    E.setdefault(prop, []).append(e)


def _tex_hint_for_ec(d, scan):
    key = (d.get("soilType") or scan.get("soilType") or "").lower().replace("-", "_")
    return _EC_FACTOR_KEY.get(key, 9.5)


def _user_entries(E, d, rejected, ctx):
    """Mediciones y pruebas de campo que escribió el usuario (formulario)."""
    m = d.get("measured") if isinstance(d.get("measured"), dict) else {}
    t = d.get("tests") if isinstance(d.get("tests"), dict) else {}
    by_lab = str(m.get("by", "")).lower() == "lab"
    src = "lab" if by_lab else "field"
    k = 1.0 if by_lab else 2.0     # un kit casero o medidor de bolsillo es ~2× menos preciso que un laboratorio
    ctx["by"] = "lab" if by_lab else "kit"
    ctx["depth"] = str(m.get("depth") or "")[:20]

    def val(prop, raw):
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            return None
        x = _num(raw)
        if x is None or not (PROPS[prop]["lo"] <= x <= PROPS[prop]["hi"]):
            rejected.append(f"{PROPS[prop]['label']}: el valor «{str(raw)[:12]}» no es válido o está fuera de rango y se ignoró")
            return None
        return x

    # pH
    ph = val("ph", d.get("ph"))
    if ph is not None:
        meth = str(d.get("phMethod") or "estimate")
        adj, sd, psrc, desc = _PH_METHODS.get(meth, _PH_METHODS["estimate"])
        _add(E, "ph", ph + adj, sd, psrc, desc)

    # conductividad eléctrica → CE del extracto de saturación (dS/m)
    ec_raw = m.get("ec")
    ec = _num(ec_raw)
    if ec is not None:
        unit = str(m.get("ecUnit") or "dS/m")
        ec = ec / 1000.0 if unit.lower().startswith("us") or unit.lower().startswith("µs") else ec
        if ec < 0 or ec > 200:
            rejected.append("Conductividad eléctrica: valor fuera de rango y se ignoró")
        else:
            meth = str(m.get("ecMethod") or "ece")
            if meth == "ece":
                ece, sd, note = ec, max(0.2, 0.10 * ec) * k, "extracto o pasta de saturación"
            else:
                f = ctx.get("ec_factor", 9.5)
                ece = ec * f if meth == "1:5" else ec * f / 2.5
                base = max(0.2, 0.10 * ece) * k
                sd = math.sqrt(base ** 2 + (0.30 * ece) ** 2)     # la conversión añade ~30 % de incertidumbre
                note = f"suspensión {meth} convertida a extracto de saturación (×{f if meth == '1:5' else round(f / 2.5, 1)}, aproximado)"
            if ece > PROPS["ec"]["hi"]:
                rejected.append("Conductividad eléctrica: valor fuera de rango y se ignoró")
            else:
                _add(E, "ec", ece, sd, src, note)
    elif ec_raw not in (None, ""):
        rejected.append("Conductividad eléctrica: valor no numérico, se ignoró")

    om = val("om", m.get("om"))
    if om is not None:
        _add(E, "om", om, max(0.15, 0.10 * om) * k, src, "materia orgánica (% de M.O.)")
    pv = val("p", m.get("p"))
    if pv is not None:
        ctx["p_method"] = "bray" if str(m.get("pMethod")) == "bray" else "olsen"
        _add(E, "p", pv, 0.15 * pv * k, src, "fósforo " + ("Bray" if ctx["p_method"] == "bray" else "Olsen"))
    kv = val("k", m.get("k"))
    if kv is not None:
        _add(E, "k", kv, 0.15 * kv * k, src, "potasio intercambiable")
    nv = val("n", m.get("n"))
    if nv is not None:
        _add(E, "n", nv, 0.25 * nv * k, src, "nitrógeno inorgánico (varía mucho con la época)")
    cv = val("cec", m.get("cec"))
    if cv is not None:
        _add(E, "cec", cv, 0.10 * cv * k, src, "CIC a pH 7")
    for prop, key in (("clay", "clay"), ("sand", "sand"), ("silt", "silt")):
        x = val(prop, m.get(key))
        if x is not None:
            _add(E, prop, x, 3.0 * k, src, "textura por análisis (hidrómetro/pipeta)")

    # pruebas rápidas de campo
    jar = {p: val(p, t.get("jar" + p.capitalize())) for p in ("clay", "sand", "silt")}
    got = {p: x for p, x in jar.items() if x is not None}
    if len(got) >= 2:
        if len(got) == 3 and not (85 <= sum(got.values()) <= 115):
            rejected.append(f"Prueba del frasco: arcilla + arena + limo suman {round(sum(got.values()))} % (deben sumar ≈ 100) y se ignoró")
        else:
            for p, x in got.items():
                _add(E, p, x, 8.0, "field", "prueba del frasco (sedimentación)")
    elif got:
        rejected.append("Prueba del frasco: faltan al menos dos de los tres porcentajes y se ignoró")
    rb = _RIBBON.get(str(t.get("ribbon") or ""))
    if rb:
        _add(E, "clay", rb[0], rb[1], "test", "prueba de la cinta: " + rb[2])
    vin = str(t.get("vinegar") or "")
    if vin in ("ninguna", "leve", "fuerte"):
        ctx["fizz"] = vin
    crust = str(t.get("crust") or "")
    if crust in ("si", "no"):
        ctx["crust_user"] = crust


def _scan_entries(E, d, scan, ph_sd):
    """Lo que la IA estimó desde la foto y lo que el usuario declaró: nivel bajo, pesa poco y solo se usa si no hay nada mejor."""
    ph = _num(scan.get("estimatedPh"), PROPS["ph"]["lo"], PROPS["ph"]["hi"])
    if ph is not None:
        _add(E, "ph", ph, ph_sd, "ai", "estimación visual (color/estructura): poco fiable")
    om = _OM_PHOTO.get(str(scan.get("organicMatter") or "").lower())
    if om:
        _add(E, "om", om[0], om[1], "ai", "oscuridad del suelo en la foto")
    key = str(d.get("soilType") or scan.get("soilType") or "").lower().replace("-", "_")
    tex = _SOILTYPE_TEX.get(key)
    if tex:
        src = "ai" if (scan and str(scan.get("soilType") or "").lower() == key) else "declared"
        note = "tipo de suelo " + ("visto en la foto" if src == "ai" else "declarado") + f" ({key.replace('_', ' ')})"
        for p, x in zip(("clay", "sand", "silt"), tex):
            _add(E, p, x, 12.0, src, note)


def _sg_entries(E, sg):
    """Satélite: valores MODELADOS (no medidos) de soildb (30 m, 0-30 cm) o SoilGrids (250 m, 0-5 cm); usa sus cuantiles como incertidumbre."""
    if not sg:
        return
    sd = sg.get("sd") or {}
    supp = sg.get("supplement") or {}
    note = f"modelo {sg.get('source') or 'SoilGrids 250 m'}, capa {sg.get('depth') or '0-5 cm'}"
    if "ph_real" in sg:
        _add(E, "ph", sg["ph_real"], max(sd.get("ph_real", 0.8), 0.3), "modeled", note)
    for key, prop, dflt in (("clay_pct", "clay", 8.0), ("sand_pct", "sand", 11.0), ("silt_pct", "silt", 9.0)):
        if key in sg:
            _add(E, prop, sg[key], max(sd.get(key, dflt), 3.0), "modeled", note)
    if "organic_carbon_pct" in sg:
        om = sg["organic_carbon_pct"] * 1.724
        s = sd.get("organic_carbon_pct")
        _add(E, "om", om, max(s * 1.724 if s else 0.5 * om + 0.3, 0.3), "modeled", note + " (C.O. × 1.724)")
    if "cec_cmolkg" in sg:
        s = sd.get("cec_cmolkg")
        _add(E, "cec", sg["cec_cmolkg"], max(s if s else 0.5 * sg["cec_cmolkg"] + 3, 2.0), "modeled",
             f"modelo {supp['cec_cmolkg']}" if "cec_cmolkg" in supp else note)


# ── Perfiles medidos cercanos: tabla local + WoSIS (ISRIC) ─────────────────────
def _haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


_PROFILE_COLS = ("ph", "ec", "om", "clay", "sand", "silt", "cec", "p", "k", "n")


def _local_profiles(lat, lng, max_km=40.0):
    """Perfiles medidos guardados en la base de datos (aportes de la comunidad con consentimiento + CSV importados)."""
    d_lat = max_km / 111.0
    d_lng = max_km / (111.0 * max(0.2, math.cos(math.radians(lat))))
    conn = get_db()
    try:
        rows = db_execute(conn, "SELECT * FROM perfiles_medidos WHERE lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?",
                          (lat - d_lat, lat + d_lat, lng - d_lng, lng + d_lng)).fetchall()
    finally:
        conn.close()
    out = []
    for r in rows:
        r = dict(r)
        km = _haversine_km(lat, lng, r["lat"], r["lng"])
        if km <= max_km:
            out.append({
                "kind": "local", "code": f"EC-{r['id']}", "source": r.get("fuente") or "Perfiles locales",
                "km": km, "date": r.get("fecha") or None, "depth": r.get("profundidad") or None,
                "method": r.get("metodo") or None,
                "values": {c: r.get(c) for c in _PROFILE_COLS if r.get(c) is not None},
            })
    return out


_WOSIS_URL = "https://graphql.isric.org/wosis/graphql"
# atributo WoSIS → (propiedad EcoScan, factor de conversión). orgc viene en g/kg → M.O. % = orgc/10 × 1.724
_WOSIS_ATTRS = [("phaq", "ph", 1.0), ("elco", "ec", 1.0), ("orgc", "om", 0.1724), ("clay", "clay", 1.0),
                ("sand", "sand", 1.0), ("silt", "silt", 1.0), ("cecph7", "cec", 1.0)]
_WOSIS_CACHE = {}
_WOSIS_LOCK = threading.Lock()
# WoSIS (ISRIC) es un servicio público que a veces tarda más de 10 s (o no responde). Por eso:
#  · el análisis espera como máximo _WOSIS_FG_WAIT s, EN PARALELO con SoilGrids (ver analyze_soil);
#  · la consulta sigue en segundo plano hasta _WOSIS_READ_TIMEOUT s y su resultado queda en caché (memoria y base de
#    datos, 30 días): el siguiente análisis de esa zona —incluso tras reiniciar el servidor— ya lo tiene al instante;
#  · en cuanto se conoce la ubicación (/api/prefetch-profiles, /api/scan-image) se pide por adelantado;
#  · tras 2 fallos seguidos se deja de consultar 5 min (disyuntor) para no hacer esperar a nadie con el servicio caído;
#  · se recuerda qué forma de la consulta GraphQL funcionó, para no volver a "descubrir" el esquema en cada análisis.
_WOSIS_FG_WAIT = float(os.environ.get("WOSIS_WAIT", 25))
_WOSIS_READ_TIMEOUT = float(os.environ.get("WOSIS_READ_TIMEOUT", 60))
_WOSIS_DB_TTL = 30 * 86400
_WOSIS_INFLIGHT = {}        # celda → consulta en curso {"event": Event, "raw": resultado}
_WOSIS_FORM = None          # (use_nodes, atributos) de la última consulta que funcionó
_WOSIS_FAILS = 0
_WOSIS_DOWN_UNTIL = 0.0


def _wosis_query(attrs, use_nodes):
    sel = " ".join(f"{a}Values(first: 2) {{ valueAvg value }}" for a, _, _ in attrs)
    lay = (f"layers(first: 10) {{ nodes {{ upperDepth lowerDepth date {sel} }} }}" if use_nodes
           else f"layers(first: 10) {{ upperDepth lowerDepth date {sel} }}")
    body = f"profileCode datasetCode latitude longitude countryName {lay}"
    sub = f"nodes {{ {body} }}" if use_nodes else body
    return ("query($geom: GeoJSON!) { wosisLatestProfiles(first: 100, "
            "filter: {layersExist: true, geom: {intersects: $geom}}) { " + sub + " } }")


def _wosis_raw(lat, lng, half_deg=0.2):
    """Consulta GraphQL de WoSIS (perfiles de suelo MEDIDOS en laboratorio). Defensiva: si el esquema cambia o el servicio
    no responde, devuelve None y el análisis continúa sin este dato."""
    global _WOSIS_FORM
    dl = half_deg / max(0.2, math.cos(math.radians(lat)))
    ring = [[lng - dl, lat - half_deg], [lng + dl, lat - half_deg], [lng + dl, lat + half_deg],
            [lng - dl, lat + half_deg], [lng - dl, lat - half_deg]]
    geom = {"type": "MultiPolygon", "coordinates": [[ring]]}
    forms = ([_WOSIS_FORM] if _WOSIS_FORM else []) + [(False, tuple(_WOSIS_ATTRS)), (True, tuple(_WOSIS_ATTRS))]
    for use_nodes, attrs0 in forms:
        attrs = list(attrs0)
        for _ in range(len(_WOSIS_ATTRS) + 1):
            try:
                resp = _req.post(_WOSIS_URL, json={"query": _wosis_query(attrs, use_nodes), "variables": {"geom": geom}},
                                 timeout=(5, _WOSIS_READ_TIMEOUT))
                js = resp.json()
            except Exception as ex:
                print(f"[WoSIS] sin respuesta: {ex}")
                return None
            errs = js.get("errors") or []
            if not errs and js.get("data"):
                prof = (js["data"] or {}).get("wosisLatestProfiles")
                if isinstance(prof, dict):
                    prof = prof.get("nodes")
                _WOSIS_FORM = (use_nodes, tuple(attrs))      # esta forma funciona: la próxima vez se usa directamente
                return prof if isinstance(prof, list) else []
            msg = " ".join(str(e.get("message", "")) for e in errs if isinstance(e, dict))
            bad = re.findall(r'field ["\']?(\w+?)Values["\']?', msg)
            dropped = [a for a in attrs if a[0] in bad]
            if dropped:                               # un atributo no existe en el esquema: se quita y se reintenta
                attrs = [a for a in attrs if a not in dropped]
                if attrs:
                    continue
            break                                     # otro error: probar la otra forma (con/sin "nodes") o rendirse
    _WOSIS_FORM = None
    print("[WoSIS] consulta no válida para el esquema actual; se omite")
    return None


def _wosis_note_result(ok):
    """Disyuntor: 2 fallos seguidos → no se consulta WoSIS durante 5 min; un éxito lo cierra."""
    global _WOSIS_FAILS, _WOSIS_DOWN_UNTIL
    with _WOSIS_LOCK:
        if ok:
            _WOSIS_FAILS, _WOSIS_DOWN_UNTIL = 0, 0.0
        else:
            _WOSIS_FAILS += 1
            if _WOSIS_FAILS >= 2:
                _WOSIS_DOWN_UNTIL = time.time() + 300
                print("[WoSIS] 2 fallos seguidos: se omite WoSIS durante 5 min")


def _wosis_db_get(key):
    """Respuesta de WoSIS guardada en la base de datos para esta celda (o None si no hay o ya caducó)."""
    try:
        conn = get_db()
        try:
            row = db_execute(conn, "SELECT datos, ts FROM wosis_cache WHERE clave = ?", (f"{key[0]}|{key[1]}",)).fetchone()
        finally:
            conn.close()
        if row and time.time() - float(row["ts"]) < _WOSIS_DB_TTL:
            data = json.loads(row["datos"])
            return data if isinstance(data, list) else None
    except Exception as ex:
        print(f"[WoSIS] caché guardada no disponible: {ex}")
    return None


def _wosis_db_put(key, raw):
    try:
        conn = get_db()
        try:
            db_execute(conn, "INSERT INTO wosis_cache (clave, datos, ts) VALUES (?, ?, ?) "
                             "ON CONFLICT (clave) DO UPDATE SET datos = excluded.datos, ts = excluded.ts",
                       (f"{key[0]}|{key[1]}", json.dumps(raw), time.time()))
            conn.commit()
        finally:
            conn.close()
    except Exception as ex:
        print(f"[WoSIS] no se pudo guardar la caché: {ex}")


def _wosis_fetch_bg(key, lat, lng, slot):
    """Hilo en segundo plano: termina la consulta aunque el análisis ya no espere, y deja el resultado guardado."""
    t0 = time.time()
    try:
        raw = _wosis_raw(lat, lng)
    except Exception as ex:
        print(f"[WoSIS] error: {ex}")
        raw = None
    with _WOSIS_LOCK:
        if len(_WOSIS_CACHE) > 300:
            _WOSIS_CACHE.clear()
        _WOSIS_CACHE[key] = (time.time(), raw)
        _WOSIS_INFLIGHT.pop(key, None)
    if raw is not None:
        _wosis_db_put(key, raw)
        print(f"[WoSIS] listo en {time.time() - t0:.0f} s ({len(raw)} perfiles en la zona); guardado para los próximos análisis")
    _wosis_note_result(raw is not None)
    slot["raw"] = raw
    slot["event"].set()


def _wosis_raw_cached(lat, lng, wait=None, quiet=False):
    """Perfiles crudos de WoSIS para la celda de (lat, lng): de la memoria, de la base de datos, o consultando con espera
    acotada. None = sin dato por ahora. wait=0 solo arranca la consulta en segundo plano (precarga)."""
    key = (round(lat, 2), round(lng, 2))
    with _WOSIS_LOCK:
        hit = _WOSIS_CACHE.get(key)
        if hit and time.time() - hit[0] < (21600 if hit[1] is not None else 180):
            return hit[1]
    saved = _wosis_db_get(key)
    if saved is not None:
        with _WOSIS_LOCK:
            _WOSIS_CACHE[key] = (time.time(), saved)
        return saved
    with _WOSIS_LOCK:
        if time.time() < _WOSIS_DOWN_UNTIL:
            return None                                # servicio caído hace poco: ni se intenta
        slot = _WOSIS_INFLIGHT.get(key)
        if slot is None:                               # nadie la está consultando: arranca en segundo plano
            slot = {"event": threading.Event(), "raw": None}
            _WOSIS_INFLIGHT[key] = slot
            threading.Thread(target=_wosis_fetch_bg, args=(key, lat, lng, slot), daemon=True).start()
        fails = _WOSIS_FAILS
    if wait is None:
        wait = _WOSIS_FG_WAIT if fails == 0 else min(5.0, _WOSIS_FG_WAIT)   # tras un fallo se sondea con menos paciencia
    if not slot["event"].wait(wait):
        if not quiet:
            print(f"[WoSIS] ISRIC aún no responde tras {wait:.0f} s: el análisis sigue sin el perfil medido cercano. "
                  f"La consulta continúa en segundo plano y quedará guardada para la próxima vez en esta zona.")
        return None
    return slot["raw"]


def _prefetch_geodata(lat, lng):
    """Pide por adelantado, en segundo plano, lo lento (WoSIS y SoilGrids) para que el análisis ya lo encuentre listo."""
    try:
        _wosis_raw_cached(lat, lng, wait=0, quiet=True)
    except Exception as ex:
        print(f"[Prefetch] WoSIS: {ex}")

    try:
        fetch_soilgrids(lat, lng, wait=0, quiet=True)
    except Exception as ex:
        print(f"[Prefetch] satélite: {ex}")

    try:                                                       # clima y altitud: se calientan en segundo plano
        threading.Thread(target=fetch_climate, args=(lat, lng), daemon=True).start()
    except Exception as ex:
        print(f"[Prefetch] clima: {ex}")


def _wosis_candidates(lat, lng):
    raw = _wosis_raw_cached(lat, lng)
    if raw is None:
        return None
    out = []
    for p in raw:
        try:
            plat, plng = float(p["latitude"]), float(p["longitude"])
        except (TypeError, KeyError, ValueError):
            continue
        layers = p.get("layers")
        if isinstance(layers, dict):
            layers = layers.get("nodes")
        top = None
        for ly in (layers or []):
            up = _num(ly.get("upperDepth"))
            if up is not None and up <= 10 and (top is None or up < _num(top.get("upperDepth"), 0, 1e9)):
                top = ly
        if not top:
            continue
        vals = {}
        for attr, prop, factor in _WOSIS_ATTRS:
            arr = top.get(attr + "Values") or []
            for it in arr:
                v = _num((it or {}).get("valueAvg"))
                v = v if v is not None else _num((it or {}).get("value"))
                if v is not None:
                    vals[prop] = v * factor
                    break
        if not vals:
            continue
        d_top, d_bot = _num(top.get("upperDepth")), _num(top.get("lowerDepth"))
        out.append({
            "kind": "wosis", "code": str(p.get("profileCode") or "")[:40], "source": "WoSIS (ISRIC)",
            "km": _haversine_km(lat, lng, plat, plng), "date": str(top.get("date") or "")[:10] or None,
            "depth": f"{int(d_top or 0)}-{int(d_bot)} cm" if d_bot is not None else None, "method": "laboratorio",
            "values": vals,
        })
    return out


def nearest_profiles(lat, lng):
    """([perfiles medidos ordenados por distancia], estado de cada proveedor)."""
    status, cands = {}, []
    try:
        loc = _local_profiles(lat, lng)
        status["local"] = "ok" if loc else "sin_resultados"
        cands += loc
    except Exception as ex:
        print(f"[Perfiles] error local: {ex}")
        status["local"] = "no_disponible"
    if any(c["km"] <= 15 for c in cands):          # con un perfil local muy cercano no hace falta consultar WoSIS
        status["wosis"] = "omitido"
    else:
        w = _wosis_candidates(lat, lng)
        if w is None:
            status["wosis"] = "no_disponible"
        else:
            status["wosis"] = "ok" if w else "sin_resultados"
            cands += w
    cands.sort(key=lambda c: c["km"])
    return cands, status


def _dist_sd(prop, v, km, date=None):
    """Incertidumbre de usar un perfil a `km` kilómetros: el suelo cambia a escala de decenas a cientos de metros."""
    if prop == "ph":
        base = 0.45 + 0.04 * km
    elif prop in ("clay", "sand", "silt"):
        base = 6.0 + 0.5 * km
    else:
        base = abs(v) * (0.30 + 0.01 * km) + (0.2 if prop == "ec" else 0.0)
    try:
        age = datetime.date.today().year - int(str(date)[:4])
        base *= 1 + min(max(age, 0), 40) / 40 * 0.5     # los datos viejos valen menos (hasta +50 %)
    except (TypeError, ValueError):
        pass
    return base


def _nearby_entries(E, cands, max_km=30.0):
    cands = [c for c in cands if c["km"] <= max_km]
    for prop in ("ph", "ec", "om", "cec"):            # N-P-K de un perfil lejano no sirve: varían demasiado en el tiempo y el espacio
        for c in cands:
            v = c["values"].get(prop)
            if v is not None:
                _add(E, prop, v, _dist_sd(prop, v, c["km"], c.get("date")), "nearby",
                     f"{c['source']} · {c['km']:.1f} km" + (f" · {c['date'][:4]}" if c.get("date") else ""), c["km"])
                break
    for c in cands:                                   # la textura se toma completa de un mismo perfil
        v = c["values"]
        if all(v.get(p) is not None for p in ("clay", "sand", "silt")):
            for p in ("clay", "sand", "silt"):
                _add(E, p, v[p], _dist_sd(p, v[p], c["km"], c.get("date")), "nearby",
                     f"{c['source']} · {c['km']:.1f} km" + (f" · {c['date'][:4]}" if c.get("date") else ""), c["km"])
            break


# ── Fusión ────────────────────────────────────────────────────────────────────
def _fuse(prop, entries):
    """Fusiona las mediciones de UNA propiedad. Solo se combinan las fuentes del mejor nivel disponible (ponderadas por
    1/sd²); las de nivel inferior quedan como contraste y sirven para detectar contradicciones."""
    top = max(_TIER[e["src"]] for e in entries)
    pool = [e for e in entries if _TIER[e["src"]] == top]
    contrast = [e for e in entries if _TIER[e["src"]] != top]
    w = [1.0 / e["sd"] ** 2 for e in pool]
    wt = sum(w)
    mean = sum(wi * e["v"] for wi, e in zip(w, pool)) / wt
    sd = math.sqrt(1.0 / wt)
    srcs = {e["src"] for e in pool}
    if "nearby" in srcs and "modeled" in srcs:
        sd = max(sd, 0.85 * min(e["sd"] for e in pool))   # SoilGrids se entrenó con perfiles tipo WoSIS: no son independientes
    for wi, e in zip(w, pool):
        e["weight"] = round(100 * wi / wt)
    primary = max(pool, key=lambda e: 1.0 / e["sd"] ** 2)["src"]
    return mean, sd, top, primary, pool, contrast


def _confidence(prop, tier, sd, v):
    if prop == "ph":
        lvl = "alta" if sd <= 0.3 else "media" if sd <= 0.7 else "baja"
    elif prop in ("clay", "sand", "silt"):
        lvl = "alta" if sd <= 5 else "media" if sd <= 10 else "baja"
    else:
        rel = sd / max(abs(v), PROPS[prop]["floor"])
        lvl = "alta" if rel <= 0.2 else "media" if rel <= 0.5 else "baja"
    if tier == 1:
        return "baja"
    if tier == 2 and lvl == "alta":
        return "media"
    return lvl


def _pub(e):
    o = {"src": e["src"], "label": PROV_LABEL[e["src"]], "value": round(e["v"], 3), "sd": round(e["sd"], 3),
         "note": e.get("note", "")}
    if "weight" in e:
        o["weight"] = e["weight"]
    if "km" in e:
        o["km"] = e["km"]
    return o


def build_precision(d, scan, sg, cands, providers, ai_ph_sd=1.3, calibration=None):
    """Núcleo: reúne todas las fuentes, las fusiona, clasifica con la NOM-021, revisa contradicciones y puntúa la calidad."""
    E, rejected, ctx = {}, [], {}
    ctx["ec_factor"] = _tex_hint_for_ec(d, scan)
    _user_entries(E, d, rejected, ctx)
    _sg_entries(E, sg)
    _nearby_entries(E, cands)
    _scan_entries(E, d, scan, ai_ph_sd)

    props, flags, conflicts = {}, [], []
    for prop in PROP_ORDER:
        meta = PROPS[prop]
        ents = E.get(prop) or []
        if not ents:
            props[prop] = {"key": prop, "label": meta["label"], "unit": meta["unit"], "value": None, "primary": "none",
                           "tier": 0, "confidence": "sin dato", "sources": [], "contrast": [], "class": None, "basis": None}
            continue
        mean, sd, tier, primary, pool, contrast = _fuse(prop, ents)
        # la efervescencia con vinagre acota el pH (carbonatos libres → pH alcalino) cuando no hay una medición directa
        fz = ctx.get("fizz")
        note_c = None
        if prop == "ph" and fz in ("fuerte", "leve") and tier < 3:
            floor = 7.6 if fz == "fuerte" else 7.0
            if mean < floor:
                note_c = f"efervescencia {fz} con vinagre indica carbonatos: pH ajustado a ≥ {floor}"
                mean, sd = floor, max(sd, 0.5)
                primary = "test"
        o = {"key": prop, "label": meta["label"], "unit": meta["unit"], "value": round(mean, meta["dec"]),
             "sd": round(sd, 3), "low": round(max(meta["lo"], mean - 1.645 * sd), meta["dec"]),
             "high": round(min(meta["hi"], mean + 1.645 * sd), meta["dec"]),
             "primary": primary, "tier": tier, "confidence": _confidence(prop, tier, sd, mean),
             "sources": [_pub(e) for e in pool], "contrast": [_pub(e) for e in contrast]}
        if note_c:
            o["sources"].append({"src": "test", "label": PROV_LABEL["test"], "value": o["value"], "sd": o["sd"], "note": note_c})
        # contradicciones entre la fuente principal y las de menor nivel
        for e in contrast:
            z = abs(e["v"] - mean) / math.sqrt(e["sd"] ** 2 + sd ** 2)
            if z > 2.0 and e["src"] in ("modeled", "nearby", "test", "declared"):
                conflicts.append({"prop": prop, "severity": "aviso",
                                  "msg": f"{meta['label']}: {PROV_LABEL[primary].lower()} = {o['value']}, pero {PROV_LABEL[e['src']].lower()} = {round(e['v'], meta['dec'])}. "
                                         "Se usó la fuente de mayor nivel; el modelo y los perfiles lejanos describen otra escala y pueden no representar tu punto."})
        cls, basis = classify(prop, mean, ctx.get("p_method", "olsen"))
        o["class"], o["basis"] = cls, basis
        props[prop] = o

    # textura completa (arcilla + arena + limo normalizados a 100) y su clase USDA
    tex = None
    if all(props[p]["value"] is not None for p in ("clay", "sand", "silt")):
        c, s, l = props["clay"]["value"], props["sand"]["value"], props["silt"]["value"]
        t = usda_texture(c, s, l)
        if t:
            tier = min(props[p]["tier"] for p in ("clay", "sand", "silt"))
            tex = {**t, "tier": tier, "primary": props["clay"]["primary"],
                   "confidence": min((props[p]["confidence"] for p in ("clay", "sand", "silt")),
                                     key=lambda x: ["alta", "media", "baja", "sin dato"].index(x)),
                   "basis": "triángulo textural USDA"}

    # revisiones de coherencia entre propiedades
    ph, ec = props["ph"], props["ec"]
    if ctx.get("fizz") == "fuerte" and ph["value"] is not None and ph["tier"] == 3 and ph["value"] < 6.8:
        conflicts.append({"prop": "ph", "severity": "revisar", "msg": "Hubo efervescencia fuerte con vinagre (carbonatos) pero el pH medido es ácido: repite la medición o la prueba."})
    if ph["value"] is not None and ph["value"] >= 8.5 and (ec["value"] is None or ec["value"] < 4):
        conflicts.append({"prop": "ph", "severity": "aviso", "msg": "pH ≥ 8.5 con salinidad baja o sin medir puede indicar suelo sódico (exceso de sodio): conviene medir el PSI/RAS en laboratorio."})
    if ec["value"] is not None and ec["value"] >= 4 and ph["value"] is not None and ph["value"] < 8.5:
        conflicts.append({"prop": "ec", "severity": "info", "msg": "CE ≥ 4 dS/m con pH < 8.5: suelo salino (no sódico). El manejo es lavado con buen drenaje, no yeso."})
    if ctx.get("crust_user") == "si" or str(scan.get("saltCrust") or "").lower() == "si":
        flags.append("saltCrust")
        if ec["value"] is not None and ec["tier"] == 3 and ec["value"] < 2:
            conflicts.append({"prop": "ec", "severity": "info", "msg": "Viste costra blanca pero la CE medida es baja: puede ser carbonato o yeso, no sal."})
    key_decl = str(d.get("soilType") or "").lower()
    if tex and tex["tier"] == 3 and key_decl in _SOILTYPE_TEX and tex["appKey"] != key_decl:
        conflicts.append({"prop": "texture", "severity": "info", "msg": f"Declaraste suelo «{key_decl.replace('_', ' ')}» pero la textura medida es «{tex['es']}»: se usó la medida."})
    if key_decl == "calcareo" and ph["value"] is not None and ph["value"] < 7.0:
        conflicts.append({"prop": "ph", "severity": "aviso", "msg": "Marcaste suelo calcáreo pero el pH es < 7: revisa el tipo de suelo o el pH."})

    # calidad global de los datos (0-100): cuánto de lo que importa viene de mediciones y no de estimaciones
    score = 0.0
    for prop, meta in PROPS.items():
        p = props[prop]
        if p["primary"] == "none":
            continue
        f = _TIER_FACTOR[p["primary"]]
        if p["primary"] == "nearby":
            km = next((s.get("km") for s in p["sources"] if s["src"] == "nearby" and s.get("km") is not None), 20)
            f *= max(0.4, 1 - km / 40)
        score += meta["w"] * f
    score = round(score)
    level = "alta" if score >= 70 else "media" if score >= 45 else "baja"
    direct = [p["label"] for p in props.values() if p["primary"] in ("lab", "field")]
    nearm = [p["label"] for p in props.values() if p["primary"] == "nearby"]
    modl = [p["label"] for p in props.values() if p["primary"] == "modeled"]
    weak = [p["label"] for p in props.values() if p["primary"] in ("ai", "declared", "test")]
    miss = [p["label"] for p in props.values() if p["primary"] == "none"]
    todo = []
    if props["ph"]["tier"] < 3:
        todo.append("Mide el pH (tiras o medidor de ~US$10–20): es el dato que más cambia las recomendaciones.")
    if props["ec"]["tier"] < 3:
        todo.append("Mide la conductividad eléctrica (medidor 1:5 agua:suelo): una foto no puede distinguir un suelo salino de uno normal.")
    if any(props[p]["tier"] < 3 for p in ("n", "p", "k")):
        todo.append("Para N-P-K usa un análisis de fertilidad de laboratorio (NOM-021): ningún modelo ni foto los mide.")
    if tex is None or tex["tier"] < 3:
        todo.append("Haz la prueba del frasco o de la cinta para fijar la textura sin laboratorio.")
    n_direct = len(direct)
    prec = {
        "version": 1, "properties": props, "texture": tex, "conflicts": conflicts[:6], "flags": flags,
        "rejected": rejected[:6],
        "dataQuality": {
            "score": score, "level": level, "direct": direct, "nearby": nearm, "modeled": modl, "weak": weak,
            "missing": miss, "howToImprove": todo[:3],
            "message": (f"{n_direct} de {len(PROPS)} propiedades medidas directamente, {len(nearm)} por perfiles cercanos, "
                        f"{len(modl)} modeladas por satélite, {len(weak)} solo estimadas y {len(miss)} sin dato."),
        },
        "providers": providers or {},
        "calibration": calibration if calibration and calibration.get("n", 0) >= 5 else None,
        "pMethod": ctx.get("p_method", "olsen"),
        "userDepth": ctx.get("depth") or None,
    }
    prec["nearest"] = _nearest_card(cands, props)
    return prec


def _nearest_card(cands, props):
    if not cands:
        return {"found": False, "searchedKm": 30}
    c = cands[0]
    vals = {k: round(v, PROPS[k]["dec"]) for k, v in c["values"].items()}
    cmp_ = []
    for k, v in vals.items():
        mine = props.get(k, {})
        if mine.get("value") is not None and mine.get("primary") not in ("nearby",):
            cmp_.append({"prop": k, "nearest": v, "yours": mine["value"], "src": mine["primary"]})
    return {"found": True, "km": round(c["km"], 1), "source": c["source"], "code": c.get("code"), "date": c.get("date"),
            "depth": c.get("depth"), "method": c.get("method"), "values": vals, "compare": cmp_,
            "within30": c["km"] <= 30,
            "note": ("Perfil a más de 30 km: es solo una referencia regional, no se usó en el cálculo." if c["km"] > 30
                     else "El suelo cambia a escala de decenas a cientos de metros: cuanto más lejos el perfil, menos pesa en el cálculo.")}


# ── Prompt de IA y corrección autoritativa del resultado ───────────────────────
def build_precision_context(prec):
    """Tabla de datos verificados para el prompt: la IA razona sobre ella, no inventa cifras."""
    L = ["TABLA DE DATOS VERIFICADOS (procedencia · incertidumbre · clase). Es la VERDAD BASE del análisis:"]
    for prop in PROP_ORDER:
        p = prec["properties"][prop]
        if prop in ("clay", "sand", "silt"):
            continue
        if p["value"] is None:
            L.append(f"- {p['label']}: SIN MEDICIÓN (no inventes una cifra)")
        else:
            cls = f" → {p['class']}" if p.get("class") else ""
            L.append(f"- {p['label']}: {p['value']} {p['unit']} (±{round(p['sd'], 2)}; 90 %: {p['low']}–{p['high']}) "
                     f"[{PROV_LABEL[p['primary']]}; confianza {p['confidence']}]{cls}")
    t = prec.get("texture")
    if t:
        L.append(f"- Textura: arcilla {t['clay']} %, arena {t['sand']} %, limo {t['silt']} % → {t['es']} "
                 f"[{PROV_LABEL[t['primary']]}; confianza {t['confidence']}]")
    else:
        L.append("- Textura: SIN DATO suficiente")
    if prec["conflicts"]:
        L.append("CONTRADICCIONES DETECTADAS: " + " | ".join(c["msg"] for c in prec["conflicts"][:3]))
    if "saltCrust" in prec["flags"]:
        L.append("OBSERVACIÓN: costra blanca en la superficie (indicio de sales, NO es una medición de CE).")
    q = prec["dataQuality"]
    L.append(f"Calidad global de los datos: {q['score']}/100 ({q['level']}).")
    L.append("REGLAS DE PRECISIÓN (obligatorias):\n"
             "A. Usa los valores y clases de la tabla tal cual. Si la procedencia es «Modelado» o «Estimado», habla con cautela (\"según el modelo satelital…\", \"probablemente…\").\n"
             "B. En propiedades SIN MEDICIÓN no des cifras (ni dS/m, ni mg/kg, ni ppm): solo orientación cualitativa y di qué medir y cómo.\n"
             "C. Si hay contradicciones, manda la medición directa; menciónalo en mainIssues.\n"
             "D. Toda enmienda con dosis debe indicar de qué dato depende (p. ej. \"si el pH medido sigue > 8.0\") y aclarar que la dosis exacta requiere análisis de laboratorio.")
    return "\n".join(L)


def apply_precision_to_result(result, prec):
    """El servidor corrige lo que la IA no puede saber desde una foto: salinidad, N-P-K y materia orgánica salen de datos
    verificados o se declaran «sin medición»; nunca de una cifra inventada."""
    ss = result.get("soilStatus")
    if not isinstance(ss, dict):
        ss = result["soilStatus"] = {}
    props = prec["properties"]
    sal = ss.get("salinity") if isinstance(ss.get("salinity"), dict) else {}
    ec = props["ec"]
    if ec["value"] is not None:
        sal.update({"level": _ec_level(ec["value"]), "estimatedDsM": ec["value"], "classLabel": ec["class"],
                    "source": ec["primary"], "interval": [ec["low"], ec["high"]], "confidence": ec["confidence"],
                    "basis": "CE " + PROV_LABEL[ec["primary"]].lower()})
    else:
        order = ["baja", "media", "alta", "muy alta"]
        lvl = str(sal.get("level") or "").lower()
        lvl = lvl if lvl in order else "baja"
        crust = "saltCrust" in prec["flags"]
        if crust and order.index(lvl) < 1:
            lvl = "media"
        if not crust and order.index(lvl) > 1:
            lvl = "media"                              # sin medición ni indicio no se afirma salinidad alta
        sal.update({"level": lvl, "estimatedDsM": None, "classLabel": None, "source": "ai" if not crust else "test",
                    "interval": None, "confidence": "baja",
                    "basis": ("costra blanca observada: indicio, no es una medición de CE" if crust
                              else "sin medición de CE: nivel orientativo inferido del contexto")})
    ss["salinity"] = sal

    nut = ss.get("nutrients") if isinstance(ss.get("nutrients"), dict) else {}
    pm = prec.get("pMethod", "olsen")
    detail = {}
    for prop, key in (("n", "nitrogen"), ("p", "phosphorus"), ("k", "potassium")):
        p = props[prop]
        if p["value"] is not None and p["tier"] == 3:      # N-P-K solo se clasifican con una medición directa
            nut[key] = _nutrient_level(prop, p["value"], pm)
            detail[key] = {"value": p["value"], "unit": p["unit"], "class": p["class"], "source": p["primary"],
                           "confidence": p["confidence"]}
        else:
            nut[key] = "sin dato"
            detail[key] = {"value": None, "unit": p["unit"], "class": None, "source": "none", "confidence": "sin dato"}
    om = props["om"]
    if om["value"] is not None:
        nut["organicMatter"] = _om_level(om["value"])
        detail["organicMatter"] = {"value": om["value"], "unit": "%", "class": om["class"], "source": om["primary"],
                                   "confidence": om["confidence"]}
        if detail["nitrogen"]["value"] is None:
            detail["nitrogen"]["potential"] = _om_level(om["value"])    # solo orientativo: la M.O. aporta N al mineralizarse
    nut["detail"] = detail
    ss["nutrients"] = nut
    if not isinstance(ss.get("measurementAdvice"), list) or not ss["measurementAdvice"]:
        ss["measurementAdvice"] = prec["dataQuality"]["howToImprove"]
    sui = result.get("suitability")
    if isinstance(sui, dict):
        sui["confidence"] = prec["dataQuality"]["level"]
    return result


# ── Calibración de la IA y aportes de la comunidad (solo con consentimiento) ──
_CAL_CACHE = [0.0, None]


def calibration_stats():
    """Error de la IA al estimar el pH desde la foto frente a mediciones reales aportadas por los usuarios."""
    if _CAL_CACHE[1] is not None and time.time() - _CAL_CACHE[0] < 600:
        return _CAL_CACHE[1]
    stats = {"n": 0}
    try:
        conn = get_db()
        try:
            rows = db_execute(conn, "SELECT ph_ia, ph_med FROM calibraciones ORDER BY id DESC LIMIT 500").fetchall()
        finally:
            conn.close()
        errs = [float(r["ph_ia"]) - float(r["ph_med"]) for r in rows if r["ph_ia"] is not None and r["ph_med"] is not None]
        if errs:
            n = len(errs)
            stats = {"n": n, "bias": round(sum(errs) / n, 2), "mae": round(sum(abs(x) for x in errs) / n, 2),
                     "rmse": round(math.sqrt(sum(x * x for x in errs) / n), 2)}
    except Exception as ex:
        print(f"[Calibración] {ex}")
    _CAL_CACHE[0], _CAL_CACHE[1] = time.time(), stats
    return stats


def _ai_ph_sd():
    s = calibration_stats()
    return min(2.0, max(0.6, s["rmse"])) if s.get("n", 0) >= 15 else 1.3


def persist_contribution(d, scan, prec, lat, lng):
    """Guarda (anónimo, coordenadas a ~1 km) la medición del usuario si lo autorizó: alimenta el «perfil medido más
    cercano» de otros usuarios y la calibración de la IA. Nunca guarda el id ni el correo. Solo se comparten valores
    medidos con instrumento o laboratorio (no pH de tiras ni textura del frasco)."""
    if lat is None or lng is None:
        return False
    props = prec["properties"]
    share = {}
    for k in _PROFILE_COLS:
        p = props[k]
        mine = [s for s in p["sources"] if s["src"] in ("lab", "field")]
        if p["primary"] not in ("lab", "field") or not mine:
            continue
        note = " ".join(s["note"] for s in mine)
        if (k == "ph" and "tiras" in note) or (k in ("clay", "sand", "silt") and "frasco" in note):
            continue
        share[k] = p["value"]
    if not share:
        return False
    rl, rg = round(lat, 2), round(lng, 2)
    by = (d.get("measured") or {}).get("by") if isinstance(d.get("measured"), dict) else None
    conn = get_db()
    try:
        same = "IS NOT DISTINCT FROM" if USE_POSTGRES else "IS"
        dup = db_execute(conn, f"SELECT id FROM perfiles_medidos WHERE lat = ? AND lng = ? AND ph {same} ? AND ec {same} ?",
                         (rl, rg, share.get("ph"), share.get("ec"))).fetchone()
        if dup:
            return False
        cols = ["lat", "lng"] + list(_PROFILE_COLS) + ["profundidad", "fuente", "metodo", "fecha"]
        vals = [rl, rg] + [share.get(c) for c in _PROFILE_COLS] + [
            prec.get("userDepth"), "Comunidad EcoScan (sin verificar)",
            f"ph:{d.get('phMethod') or 'n/d'};por:{by or 'kit'}", datetime.date.today().isoformat()]
        db_execute(conn, f"INSERT INTO perfiles_medidos ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        ai_ph = _num(scan.get("estimatedPh"), 2.5, 11)
        if ai_ph is not None and share.get("ph") is not None:     # calibración: la IA por foto frente a la medición real
            db_execute(conn, "INSERT INTO calibraciones (ph_ia, ph_med, om_ia, om_med) VALUES (?, ?, ?, ?)",
                       (ai_ph, share["ph"], str(scan.get("organicMatter") or "")[:10] or None, share.get("om")))
        conn.commit()
        _CAL_CACHE[1] = None
        return True
    finally:
        conn.close()


def import_profiles_csv(path):
    """Importa perfiles medidos reales (INEGI, laboratorio, WoSIS descargado…) a la tabla local.
    CSV con cabecera: lat,lng,ph,ec,om,clay,sand,silt,cec,p,k,n,profundidad,fuente,fecha,metodo
    (ph en agua; ec en dS/m del extracto de saturación; om en %; textura en %; cec en cmol/kg; p y n en mg/kg; k en cmol/kg)."""
    import csv
    ok = bad = 0
    conn = get_db()
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                lat, lng = _num(r.get("lat"), -90, 90), _num(r.get("lng"), -180, 180)
                vals = {c: _num(r.get(c)) for c in _PROFILE_COLS}
                vals = {c: (v if v is not None and PROPS[c]["lo"] <= v <= PROPS[c]["hi"] else None) for c, v in vals.items()}
                if lat is None or lng is None or all(v is None for v in vals.values()):
                    bad += 1
                    continue
                cols = ["lat", "lng"] + list(_PROFILE_COLS) + ["profundidad", "fuente", "metodo", "fecha"]
                row = [round(lat, 5), round(lng, 5)] + [vals[c] for c in _PROFILE_COLS] + [
                    (r.get("profundidad") or "")[:20] or None, (r.get("fuente") or "Importado")[:60],
                    (r.get("metodo") or "")[:60] or None, (r.get("fecha") or "")[:10] or None]
                db_execute(conn, f"INSERT INTO perfiles_medidos ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", row)
                ok += 1
        conn.commit()
    finally:
        conn.close()
    print(f"  Perfiles importados: {ok} · filas descartadas: {bad}")
    return ok, bad


# ── GROQ VISION CON CONTEXTO DE BD ────────────────────────────────────────────
_VISION_MODEL = "qwen/qwen3.8-27b"   # modelo de visión vigente en Groq (qwen3.6-27b fue dado de baja el 14-sep-2026)

# Esfuerzo de razonamiento de las llamadas de visión (suelo y plantas). "low" es el valor de siempre.
# Si tu cuenta de Groq tiene un tope de salida muy bajo (OTPM) y quieres caber más escaneos por minuto, define
# VISION_EFFORT=none: el modelo no gasta tokens pensando. Aun con "low", si una respuesta se corta, se repite con "none".
_VISION_EFFORT = os.environ.get("VISION_EFFORT", "low").strip().lower()
if _VISION_EFFORT not in ("none", "low", "medium", "high"):
    _VISION_EFFORT = "low"

# Se añade a los prompts de visión: el mismo contenido en JSON compacto gasta ~20 % menos tokens de salida
_COMPACT_JSON = "\nDevuelve el JSON compacto: una sola línea, sin sangría ni saltos de línea, con todos los campos y el mismo detalle."


def call_groq_vision(image_b64: str, lang: str, region: str = "", lat: str = "", lng: str = "") -> dict:
    """
    Extracción visual — UNA sola llamada con imagen, esquema compacto (solo lo
    necesario para clasificar el suelo y compararlo contra la base de datos).
    El diagnóstico completo (con SoilGrids y la base de datos de suelos) lo hace
    después /api/analyze, que es solo texto y no reenvía la imagen. Mantener esto
    en una única llamada de visión, con un prompt corto, es clave para no exceder
    el límite de tokens por minuto (TPM) de la cuenta gratuita de Groq para
    modelos en preview como qwen3.8-27b.
    """
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]

    loc_ctx = ""
    if region:
        loc_ctx = f" Ubicación del usuario: '{region}'."
        if lat and lng:
            loc_ctx += f" GPS: {lat},{lng}."

    prompt = f"""Eres edafólogo, agrónomo y fitopatólogo experto. Analiza esta foto de suelo (responde en '{lang}').{loc_ctx}

Rechaza (isSoil:false) fotos de personas, manos, objetos, paredes, cemento, madera, metal, pasto denso, hojas, vegetación viva dominante, cielo, agua, playa o superficie artificial.
Acepta SOLO si 60%+ de la imagen es suelo real: tierra, arcilla, limo, arena agrícola, humus, sustrato.

Responde ÚNICAMENTE con JSON válido, sin texto ni markdown fuera del JSON.

Si NO es suelo:
{{"isSoil":false,"confidence":95,"rejection_reason":"por qué no es suelo analizable"}}

Si SÍ es suelo:
{{
  "isSoil": true,
  "confidence": 88,
  "soilType": "arenoso|arcilloso|limoso|franco|franco_arenoso|franco_arcilloso|organico|calcareo",
  "estimatedPh": 6.5,
  "compaction": "baja|media|alta",
  "drainage": "bueno|moderado|pobre",
  "erosion": "baja|media|alta",
  "fireHistory": "ninguno|reciente|moderado|antiguo",
  "color": "descripción del color y qué indica nutricionalmente",
  "texture": "descripción visual de granulometría y estructura",
  "humidity": "seco|húmedo|saturado",
  "saltCrust": "si|no",
  "organicMatter": "bajo|medio|alto",
  "organicMatterDesc": "lo observado visualmente sobre materia orgánica",
  "structureQuality": "excelente|buena|regular|pobre"
}}

Guía visual: negro/marrón oscuro=alta M.O.; rojizo=acidez/hierro libre; grisáceo=hidromorfismo; blanco/claro=calcáreo/salino; costras blancas=posible salinidad (saltCrust:si); grietas anchas=vertisol/compactación; grano suelto=arenoso.""" + _COMPACT_JSON

    # Mismo modelo, mismo prompt y mismos 1400 tokens de siempre. Si la cuenta de Groq limita la salida por minuto,
    # el ajuste y los reintentos los hace call_groq_vision_json (ver _groq_request); aquí no se recorta nada de antemano.
    result, _ = call_groq_vision_json(prompt, image_b64, [_VISION_MODEL], 1400, min_tokens=450, tag="ScanSoil")
    return result


# ── SEGURIDAD: límites, guardia de peticiones, cabeceras ──────────────────────
_RL = defaultdict(deque)
_RL_LOCK = threading.Lock()


def _rate_limit(bucket, key, limit, window):
    """Ventana deslizante en memoria. Devuelve 0 si se permite o los segundos que faltan para volver a intentar."""
    now = time.time()
    k = (bucket, key)
    with _RL_LOCK:
        dq = _RL[k]
        while dq and dq[0] <= now - window:
            dq.popleft()
        if len(dq) >= limit:
            return max(1, int(window - (now - dq[0])) + 1)
        dq.append(now)
        if len(_RL) > 5000:                       # limpieza para que no crezca sin límite
            for kk in [kk for kk, v in _RL.items() if not v or v[-1] < now - 3600]:
                _RL.pop(kk, None)
    return 0


def _too_many(wait):
    resp = jsonify({"success": False, "error": f"Demasiados intentos. Espera {wait} s e inténtalo de nuevo.",
                    "message": f"Demasiados intentos. Espera {wait} s e inténtalo de nuevo.", "code": "rate_limit"})
    resp.status_code = 429
    resp.headers["Retry-After"] = str(wait)
    return resp


_PUBLIC_API = {"/api/login", "/api/register", "/api/logout", "/api/check-auth", "/api/keepalive"}
_AI_PATHS = {"/api/scan-image", "/api/scan-plant", "/api/plant-form-identify", "/api/scan-plant-health",
             "/api/scan-plant-report", "/api/analyze", "/api/chat"}


def _origin_ok(origin):
    origin = origin.rstrip("/")
    if origin in ALLOWED_ORIGINS:
        return True
    try:
        return urlparse(origin).netloc.lower() == request.host.lower()
    except Exception:
        return False


@app.before_request
def _guard():
    path = request.path
    if request.content_length and request.content_length > app.config["MAX_CONTENT_LENGTH"]:
        return _e413(None)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and origin != "null" and not _origin_ok(origin):
            return jsonify({"error": "Origen no permitido", "code": "forbidden"}), 403
    if path.startswith("/api/") and path not in _PUBLIC_API:
        if "user_id" not in session:
            return jsonify({"error": "Tu sesión expiró. Inicia sesión de nuevo.", "code": "auth"}), 401
        wait = _rate_limit("api", session["user_id"], AI_RPM, 60)
        if wait:
            return _too_many(wait)
        if path in _AI_PATHS and not GROQ_CONFIGURED:
            return jsonify({"error": "La IA no está configurada: falta la variable GROQ_API_KEY (ver README / iniciar_ecoscan.bat).",
                            "code": "no_api_key"}), 503


def _csp(nonce):
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
        "font-src 'self' data: https://fonts.gstatic.com https://cdn.jsdelivr.net https://cdnjs.cloudflare.com",
        "img-src 'self' data: blob:",
        "connect-src 'self'",
        "media-src 'self' blob:",
        "object-src 'none'", "base-uri 'self'", "form-action 'self'", "frame-ancestors 'none'",
        *(["upgrade-insecure-requests"] if app.config["SESSION_COOKIE_SECURE"] else []),
    ])


@app.after_request
def _secure_headers(resp):
    h = resp.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(self), geolocation=(self), microphone=(), payment=(), usb=()")
    h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    if app.config["SESSION_COOKIE_SECURE"]:
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    nonce = getattr(g, "csp_nonce", None)
    if nonce:
        h["Content-Security-Policy"] = _csp(nonce)
    if request.path.startswith("/api/"):
        h["Cache-Control"] = "no-store"
    return resp


def _json_body():
    """Cuerpo JSON como diccionario; un cuerpo inválido da {} en vez de un error 500."""
    b = request.get_json(force=True, silent=True)
    return b if isinstance(b, dict) else {}


@app.errorhandler(400)
def _e400(e):
    return jsonify({"error": "Petición inválida", "code": "bad_request"}), 400


@app.errorhandler(404)
def _e404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "No encontrado", "code": "not_found"}), 404
    return "No encontrado", 404


@app.errorhandler(405)
def _e405(e):
    return jsonify({"error": "Método no permitido", "code": "method"}), 405


@app.errorhandler(413)
def _e413(e):
    return jsonify({"error": f"El archivo es demasiado grande (máximo {MAX_UPLOAD_MB} MB). Usa una foto más pequeña.", "code": "too_large"}), 413


@app.errorhandler(500)
def _e500(e):
    return jsonify({"error": "Error interno del servidor. Inténtalo de nuevo.", "code": "server_error"}), 500


# ── RUTAS ESTÁTICAS (lista blanca: nada más se puede descargar, ni la base de datos ni el código del servidor) ──
_HTML_CACHE = {}


def _serve_html(name):
    """Sirve una página HTML poniendo un nonce a sus <script> en línea (necesario para la CSP)."""
    path = os.path.join(BASE_DIR, name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        abort(404)
    ent = _HTML_CACHE.get(name)
    if not ent or ent[0] != mtime:
        with open(path, "r", encoding="utf-8") as f:
            ent = (mtime, f.read())
        _HTML_CACHE[name] = ent
    nonce = secrets.token_urlsafe(16)
    g.csp_nonce = nonce
    html = re.sub(r"<script(?=[\s>])(?![^>]*\bsrc\s*=)", f'<script nonce="{nonce}"', ent[1])
    resp = make_response(html)
    resp.headers["Content-Type"] = "text/html; charset=utf-8"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def root():
    return _serve_html("login.html" if "user_id" not in session else "index.html")


@app.route("/index.html")
def main_app():
    if "user_id" not in session:
        resp = make_response("", 302)
        resp.headers["Location"] = "/login.html"
        return resp
    return _serve_html("index.html")


@app.route("/login.html")
def login_page():
    return _serve_html("login.html")


_STATIC_FILES = {"app.js", "styles.css"}


@app.route("/<path:filename>")
def static_files(filename):
    if filename in _STATIC_FILES:
        resp = send_from_directory(BASE_DIR, filename)
        resp.headers["Cache-Control"] = "no-cache"      # revalida con ETag: siempre ves la última versión
        return resp
    if filename == "favicon.ico":
        return "", 204
    abort(404)


# ── AUTH ──────────────────────────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,}$")


@app.route("/api/register", methods=["POST"])
def register():
    try:
        b = _json_body()
        nombre   = re.sub(r"[\x00-\x1f\x7f]", "", str(b.get("nombre", ""))).strip()
        email    = str(b.get("email", "")).strip().lower()
        password = str(b.get("password", ""))
        if not nombre or not email or not password:
            return jsonify({"success": False, "message": "Todos los campos son obligatorios"}), 400
        if len(nombre) < 2 or len(nombre) > 80:
            return jsonify({"success": False, "message": "El nombre debe tener entre 2 y 80 caracteres"}), 400
        if len(email) > 254 or not _EMAIL_RE.match(email):
            return jsonify({"success": False, "message": "Correo inválido"}), 400
        if len(password) < 8 or len(password) > 128:
            return jsonify({"success": False, "message": "La contraseña debe tener entre 8 y 128 caracteres"}), 400
        if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
            return jsonify({"success": False, "message": "La contraseña debe incluir al menos una letra y un número"}), 400
        wait = _rate_limit("register", request.remote_addr or "?", 10, 600)   # solo cuentan los intentos con datos válidos
        if wait:
            return _too_many(wait)
        conn = get_db()
        try:
            if db_execute(conn, "SELECT id FROM usuarios WHERE email=?", (email,)).fetchone():
                return jsonify({"success": False, "message": "Correo ya registrado"}), 409
            if USE_POSTGRES:
                cur = db_execute(
                    conn, "INSERT INTO usuarios(nombre,email,password) VALUES(?,?,?) RETURNING id",
                    (nombre, email, hash_password(password)),
                )
                uid = cur.fetchone()["id"]
            else:
                cur = db_execute(
                    conn, "INSERT INTO usuarios(nombre,email,password) VALUES(?,?,?)",
                    (nombre, email, hash_password(password)),
                )
                uid = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
        session.clear()
        session["user_id"] = uid
        session["user_nombre"] = nombre
        session["user_email"] = email
        session.permanent = True
        return jsonify({"success": True, "message": "Registro exitoso", "user": {"nombre": nombre, "email": email}})
    except Exception as e:
        print(f"Register error: {e}")
        return jsonify({"success": False, "message": "Error interno"}), 500


@app.route("/api/login", methods=["POST"])
def login():
    try:
        b = _json_body()
        email    = str(b.get("email", "")).strip().lower()[:254]
        password = str(b.get("password", ""))[:128]
        if not email or not password:
            return jsonify({"success": False, "message": "Correo y contraseña requeridos"}), 400
        wait = _rate_limit("login-ip", request.remote_addr or "?", 15, 300) or _rate_limit("login-mail", email, 8, 600)
        if wait:
            return _too_many(wait)
        conn = get_db()
        try:
            u = db_execute(
                conn, "SELECT id,nombre,email,password FROM usuarios WHERE email=?", (email,)
            ).fetchone()
            if not u:
                _dummy_verify(password)
                return jsonify({"success": False, "message": "Credenciales incorrectas"}), 401
            if not verify_password(u["password"], password):
                return jsonify({"success": False, "message": "Credenciales incorrectas"}), 401
            if _is_legacy_hash(u["password"]):
                # Migra el SHA-256 antiguo a un hash con sal en el primer inicio de sesión correcto
                try:
                    db_execute(conn, "UPDATE usuarios SET password=? WHERE id=?", (hash_password(password), u["id"]))
                    conn.commit()
                except Exception as e:
                    print(f"[Login] No se pudo actualizar el hash antiguo: {e}")
        finally:
            conn.close()
        session.clear()
        session["user_id"] = u["id"]
        session["user_nombre"] = u["nombre"]
        session["user_email"] = u["email"]
        session.permanent = True
        return jsonify({"success": True, "message": "Login exitoso", "user": {"nombre": u["nombre"], "email": u["email"]}})
    except Exception as e:
        print(f"Login error: {e}")
        return jsonify({"success": False, "message": "Error interno"}), 500


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True})


@app.route("/api/check-auth", methods=["GET"])
def check_auth():
    if "user_id" in session:
        return jsonify({
            "authenticated": True,
            "user": {
                "id": session["user_id"],
                "nombre": session["user_nombre"],
                "email": session["user_email"],
            },
        })
    return jsonify({"authenticated": False})


@app.route("/api/keepalive", methods=["GET"])
def keepalive():
    """
    Endpoint sin autenticación para servicios externos de monitoreo gratuitos
    (por ejemplo UptimeRobot). Hace una consulta real a la base de datos, no
    solo responde "ok", porque proveedores gratuitos como Supabase pausan el
    proyecto tras 7 días SIN actividad de base de datos — un ping que solo
    toca la app (sin consultar la BD) no evita esa pausa.
    """
    try:
        conn = get_db()
        try:
            db_execute(conn, "SELECT 1")
        finally:
            conn.close()
        return jsonify({"status": "ok"})
    except Exception as e:
        print(f"[Keepalive] Error: {e}")
        return jsonify({"status": "error"}), 500


# ── SCAN IMAGEN · FASE 1 (solo visión: valida que sea suelo) ────────────────────────────────────
@app.route("/api/scan-image", methods=["POST"])
def scan_image():
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        body      = _json_body()
        image_b64 = body.get("image", "")
        lang      = body.get("lang", "es")
        region    = body.get("region", "")
        lat_s     = body.get("lat", "")
        lng_s     = body.get("lng", "")

        if not image_b64:
            return jsonify({"error": "No se recibió imagen"}), 400

        # Si ya se conoce la ubicación, WoSIS y SoilGrids se piden ahora, mientras la IA mira la foto
        _la, _lo = _num(lat_s, -90, 90), _num(lng_s, -180, 180)
        if _la is not None and _lo is not None:
            _prefetch_geodata(_la, _lo)

        # 1) Extracción visual (única llamada con imagen — ver call_groq_vision; maneja los límites de Groq por dentro)
        initial_result = call_groq_vision(image_b64, lang, region, lat_s, lng_s)

        return jsonify(initial_result)

    except json.JSONDecodeError as e:
        print(f"[Scan] JSON error: {e}")
        return jsonify({"error": "Respuesta de IA inválida al analizar imagen"}), 500
    except Exception as e:
        err_str = str(e).lower()
        print(f"[Scan] Error: {e}")
        # Límite de tokens/velocidad: error real y accionable, NO lo disfraces de "no es suelo"
        if any(k in err_str for k in ["rate_limit", "429", "413", "too large",
                                       "tokens per minute", "quota", "request too large"]):
            return jsonify({"error": AI_CONN_ERROR, "code": "rate_limit"}), 503
        if any(k in err_str for k in ["vision", "unsupported image", "invalid_image", "image_url"]):
            return jsonify({
                "isSoil": False, "confidence": 0,
                "rejection_reason": "Análisis visual no disponible en este momento. Usa el formulario.",
            })
        return jsonify({"error": "No se pudo analizar la imagen. Inténtalo de nuevo.", "code": "server_error"}), 500


# ── CAMPOS QUE PRODUCE LA FASE VISUAL DEL ESCÁNER DE SUELO ───────────────────
# El cliente los reenvía a /api/analyze (sin la imagen) para enriquecer el análisis.
_SCAN_FIELDS = {"isSoil", "confidence", "soilType", "estimatedPh", "compaction", "drainage", "erosion",
                "fireHistory", "color", "texture", "humidity", "organicMatter", "organicMatterDesc",
                "structureQuality", "saltCrust"}


# ── ESCÁNER DE PLANTAS · 3 etapas ────────────────────────────────────────────
# Etapa 1: IA con visión (UNA sola llamada con imagen, igual que en suelos): valida que sea una planta, la
#          identifica y describe manchas, formas y colores.                        (/api/scan-plant)
# Etapa 2: segunda IA (modelo de razonamiento distinto, solo texto) diagnostica enfermedades y problemas a partir
#          de esas observaciones y propone el tratamiento.                         (/api/scan-plant-health)
# Etapa 3: base de datos de plantas + informe de cuidados y de maceta.             (/api/scan-plant-report)
# Una sola llamada con imagen evita el límite de tokens por minuto de Groq (cada imagen cuesta 2048 tokens).
# PLANT_ID_MODEL / PLANT_HEALTH_MODEL permiten cambiar el modelo de cada etapa desde el entorno.
_PLANT_ID_MODELS     = list(dict.fromkeys([os.environ.get("PLANT_ID_MODEL", _VISION_MODEL), _VISION_MODEL]))
_PLANT_HEALTH_MODELS = list(dict.fromkeys([os.environ.get("PLANT_HEALTH_MODEL", _GROQ_TEXT_MODELS[0])] + _GROQ_TEXT_MODELS))
_DEAD_MODELS = set()
_PLANT_FIELDS = {"isPlant", "confidence", "commonName", "scientificName", "family", "plantType", "lifeCycle",
                 "origin", "habitat", "description", "isPotted", "potObserved"}


def _clean_fields(src, allowed, n=200):
    """Conserva solo campos permitidos de tipo simple y con largo acotado (los datos vienen del cliente)."""
    if not isinstance(src, dict):
        return {}
    return {k: (v[:n] if isinstance(v, str) else v) for k, v in src.items()
            if k in allowed and isinstance(v, (str, int, float, bool))}


def call_groq_vision_json(prompt, image_b64, models, max_tokens=1600, min_tokens=500, tag="GroqVision", max_wait=45.0):
    """
    Llamada de visión que devuelve (dict, modelo). Qué hace ante los problemas típicos de Groq:
      · tope de tokens de salida de la cuenta (429 «Request too large … OTPM»): ajusta max_tokens con las cifras del error
        y reintenta al instante, sin esperar (ver _groq_request);
      · 429 por velocidad real: espera lo que Groq indique (hasta max_wait s) y repite una vez;
      · respuesta cortada por el tope de tokens: repite con el razonamiento desactivado (más espacio para el JSON);
      · modelo retirado: lo descarta y prueba el siguiente.
    """
    if "," in image_b64:
        image_b64 = image_b64.split(",")[1]
    messages = [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
    ]}]
    efforts = [_VISION_EFFORT] + (["none"] if _VISION_EFFORT != "none" else [])
    last_err = RuntimeError("Sin modelos de visión disponibles")
    for model in [m for m in models if m not in _DEAD_MODELS] or models:
        for effort in efforts:
            try:
                r, used = _groq_request(model, messages, max_tokens, temperature=0.3, top_p=0.95, json_mode=True,
                                        effort=effort, min_tokens=min_tokens, tag=tag, max_wait=max_wait)
                choice = r.choices[0]
                if choice.finish_reason == "length" and effort != "none":
                    print(f"[{tag}] {model}: la respuesta se cortó con razonamiento '{effort}' (tope {used} tokens); "
                          f"se repite sin razonamiento")
                    last_err = RuntimeError("respuesta de visión truncada")
                    continue
                return parse_ai_json(choice.message.content or ""), model
            except json.JSONDecodeError as e:
                last_err = e
                if effort != "none":
                    print(f"[{tag}] {model}: JSON inválido con razonamiento '{effort}'; se repite sin razonamiento")
                    continue
                break
            except Exception as e:
                last_err = e
                if not _is_json_invalid(e) and any(k in str(e).lower() for k in ["decommission", "not found", "does not exist", "deprecat"]):
                    _DEAD_MODELS.add(model)
                print(f"[{tag}] {model} falló, probando siguiente... ({e})")
                break
    raise last_err


def _clean_obs(o):
    """Observaciones visuales de la etapa 1 (llegan del cliente): solo texto y con largo acotado."""
    if not isinstance(o, dict):
        return {}
    out = _clean_fields(o, {"overall", "leaves", "stemsAndBase", "substrate", "pestsSeen"}, 400)
    out["spots"] = [c for c in (_clean_fields(sp, {"area", "color", "shape", "pattern"}, 120)
                                for sp in (o.get("spots") or [])[:6]) if c]
    return out


def _ai_error(e, tag):
    """Respuesta de error para fallos de IA. Un límite de velocidad o un corte de red se muestran como fallo de conexión."""
    print(f"[{tag}] Error: {e}")
    s = str(e).lower()
    if isinstance(e, json.JSONDecodeError) or _is_json_invalid(e):
        return jsonify({"error": "La IA devolvió una respuesta inválida. Inténtalo de nuevo.", "code": "ai_error"}), 500
    if _is_rate_limit(e):
        code = "rate_limit"
    elif any(k in s for k in ["connection", "timed out", "timeout", "network", "ssl", "name resolution", "unreachable"]):
        code = "connection"
    else:
        return jsonify({"error": "La IA no pudo completar la solicitud. Inténtalo de nuevo en un momento.", "code": "ai_error"}), 500
    return jsonify({"error": AI_CONN_ERROR, "code": code}), 503


# ── Base de datos de plantas: cuidados y características de la maceta ────────
def _pl(pid, name, keys, family, types, light, water, hum, temp, substrate, fert, pot, issues, toxic="", generic=False):
    return {"id": pid, "name": name, "keys": keys, "family": family, "types": types, "light": light, "water": water,
            "humidity": hum, "temp": temp, "substrate": substrate, "fertilizer": fert,
            "pot": dict(zip(("material", "size", "depth", "drainage", "repot"), pot)),
            "issues": issues, "toxic": toxic, "generic": generic}


PLANT_DB = [
    _pl("monstera", "Monstera (Costilla de Adán)", ["monstera deliciosa", "monstera", "costilla de adan"], "Araceae",
        ["tropical de follaje", "trepadora"],
        "Luz brillante indirecta; tolera media sombra", "Cuando los 3-5 cm superiores del sustrato estén secos (cada 7-10 días)",
        "60 % o más; agrupa plantas o nebuliza", "18-27 °C; no bajar de 12 °C",
        "Aireado: fibra de coco, corteza de pino, perlita y humus", "Abono líquido balanceado cada 3-4 semanas en primavera-verano",
        ("Cerámica o plástico con orificios", "4-5 cm más ancha que el cepellón; 30-40 cm en adultas", "Profunda, con tutor de musgo",
         "Varios orificios y capa de arlita", "Cada 2 años en primavera"),
        ["hojas amarillas por exceso de riego", "puntas secas por aire seco", "cochinilla algodonosa", "pudrición de raíz"],
        "Tóxica para mascotas y niños (oxalatos)"),
    _pl("potos", "Potos (Epipremnum)", ["epipremnum aureum", "epipremnum", "potos", "pothos"], "Araceae",
        ["trepadora", "tropical de follaje"],
        "Luz media a brillante indirecta; tolera poca luz", "Cuando los 2-3 cm superiores estén secos (cada 7-10 días)",
        "40-60 %", "15-29 °C", "Universal con perlita (30 %)", "Abono líquido cada 4-6 semanas en primavera-verano",
        ("Plástico o cerámica; colgante o con tutor", "15-20 cm de diámetro", "Media (15-20 cm)", "Orificios y platito que se vacíe", "Cada 2 años"),
        ["hojas amarillas por exceso de riego", "hojas pálidas por poca luz", "cochinilla", "pudrición de tallo"],
        "Tóxico para mascotas (oxalatos)"),
    _pl("sansevieria", "Lengua de suegra (Sansevieria)", ["dracaena trifasciata", "sansevieria", "lengua de suegra", "lengua de tigre"],
        "Asparagaceae", ["suculenta", "tropical de follaje"],
        "Desde sombra hasta sol directo suave", "Cada 2-3 semanas; deja secar por completo", "Normal (30-50 %)", "15-30 °C",
        "Drenante tipo cactus (50 % arena gruesa o perlita)", "2-3 veces al año, diluido",
        ("Terracota pesada", "Ajustada: 2-3 cm más ancha que las raíces", "Poco profunda (15-25 cm)",
         "Orificios amplios; nunca platito con agua", "Cada 3-4 años, solo si rompe la maceta"),
        ["pudrición de raíz por exceso de riego", "hojas arrugadas por falta de agua", "cochinilla"],
        "Levemente tóxica para mascotas"),
    _pl("spathiphyllum", "Cuna de Moisés (Spathiphyllum)", ["spathiphyllum", "cuna de moises", "lirio de la paz"], "Araceae",
        ["tropical de follaje", "planta de flor"],
        "Media sombra a luz indirecta; sin sol directo", "Cuando 2 cm estén secos; sustrato ligeramente húmedo", "50-60 %", "18-27 °C",
        "Rico en materia orgánica y drenante (fibra de coco con perlita)", "Cada 4-6 semanas en primavera-verano",
        ("Plástico o cerámica", "2-3 cm más ancha que el cepellón", "Media (20-25 cm)", "Orificios y platito que se vacíe",
         "Cada 1-2 años tras la floración"),
        ["puntas marrones por cloro o aire seco", "hojas caídas por sed", "sin flores por poca luz", "trips y ácaros"],
        "Tóxica para mascotas"),
    _pl("ficus_elastica", "Hule (Ficus elastica)", ["ficus elastica", "ficus", "hule", "arbol del caucho"], "Moraceae",
        ["árbol", "tropical de follaje"],
        "Luz brillante indirecta", "Cuando 3-5 cm estén secos (cada 7-10 días)", "40-60 %", "16-27 °C; evita corrientes de aire",
        "Universal con perlita", "Mensual en primavera-verano",
        ("Cerámica pesada (da estabilidad)", "4-5 cm más ancha que el cepellón", "Profunda (30 cm o más)",
         "Orificios y capa de grava", "Cada 2 años"),
        ["caída de hojas por cambios bruscos", "manchas marrones por exceso de riego", "cochinilla y ácaros"],
        "Su látex irrita la piel y es tóxico para mascotas"),
    _pl("malamadre", "Malamadre (Chlorophytum comosum)", ["chlorophytum comosum", "chlorophytum", "malamadre", "cinta"], "Asparagaceae",
        ["tropical de follaje"],
        "Luz brillante indirecta", "Cuando 2-3 cm estén secos", "Normal; tolera aire seco", "13-27 °C",
        "Universal con perlita", "Cada 4 semanas en primavera-verano",
        ("Plástico o cerámica; también colgante", "15-20 cm; sus raíces carnosas llenan rápido la maceta", "Media (15-20 cm)",
         "Orificios de drenaje", "Cada año"),
        ["puntas marrones por cloro, flúor o aire seco", "pulgones"], "No tóxica para mascotas"),
    _pl("aloe", "Sábila (Aloe vera)", ["aloe vera", "aloe", "sabila"], "Asphodelaceae", ["suculenta"],
        "Sol directo suave o luz muy brillante", "Cada 2-3 semanas; deja secar el sustrato", "Baja", "13-29 °C; protege de heladas",
        "Arenoso y drenante (50 % arena gruesa o perlita)", "2 veces al año, diluido",
        ("Terracota", "Ancha y poco profunda; 2-3 cm más que la roseta", "Poco profunda (15-20 cm)", "Orificios amplios", "Cada 2-3 años"),
        ["hojas blandas por exceso de agua", "hojas delgadas por poca luz", "cochinilla"],
        "Su látex es tóxico para mascotas si lo ingieren"),
    _pl("echeveria", "Echeveria", ["echeveria"], "Crassulaceae", ["suculenta"],
        "Sol directo 4-6 h", "Cada 10-14 días: riega y deja secar", "Baja", "10-27 °C; sin heladas",
        "Mezcla para cactus muy drenante", "1-2 veces en primavera con abono para cactus",
        ("Terracota", "2 cm más ancha que la roseta", "Poco profunda (10-12 cm)", "Orificios amplios", "Cada 1-2 años"),
        ["tallo alargado por falta de luz", "pudrición de la base por exceso de riego", "cochinilla"], "No tóxica"),
    _pl("helecho_boston", "Helecho de Boston (Nephrolepis)", ["nephrolepis exaltata", "nephrolepis", "helecho de boston", "helecho"],
        "Nephrolepidaceae", ["helecho"],
        "Luz indirecta brillante; sin sol directo", "Mantén el sustrato húmedo sin encharcar", "60 % o más", "16-24 °C",
        "Rico en humus y fibra de coco, ligeramente ácido", "Cada 4 semanas, muy diluido",
        ("Colgante de plástico o cerámica", "Ancha, 3-4 cm más que el cepellón", "Poco profunda (15-20 cm)", "Orificios y platito", "Cada año en primavera"),
        ["frondas secas por aire seco", "amarillamiento por exceso de agua", "cochinillas"], "No tóxico para mascotas"),
    _pl("orquidea", "Orquídea (Phalaenopsis)", ["phalaenopsis", "orquidea"], "Orchidaceae", ["orquídea"],
        "Luz brillante indirecta", "Cada 7-10 días por inmersión 10-15 min y deja drenar", "50-70 %", "18-28 °C",
        "Corteza de pino gruesa (no usar tierra)", "Fertilizante para orquídeas, muy diluido, cada 2 semanas",
        ("Plástico transparente con ranuras", "Ajustada: 2 cm más ancha que las raíces", "Media", "Ranuras laterales y en el fondo",
         "Cada 1-2 años tras la floración"),
        ["raíces secas y grises por poco riego", "pudrición por exceso de agua", "hojas arrugadas", "cochinilla"], "No tóxica"),
    _pl("albahaca", "Albahaca (Ocimum basilicum)", ["ocimum basilicum", "ocimum", "albahaca"], "Lamiaceae", ["hierba aromática"],
        "Sol directo 6 h o más", "Sustrato húmedo sin encharcar (cada 2-3 días en calor)", "Media", "18-30 °C; sensible al frío",
        "Rico en materia orgánica y drenante", "Abono orgánico cada 2-3 semanas",
        ("Terracota o plástico", "20-30 cm de diámetro", "20 cm o más", "Orificios de drenaje", "Se renueva cada año; pellizca las flores"),
        ["mildiú", "hojas amarillas por exceso de agua", "pulgones", "mosca blanca"], "No tóxica"),
    _pl("nopal", "Nopal (Opuntia)", ["opuntia ficus-indica", "opuntia", "nopal"], "Cactaceae", ["cactus"],
        "Sol directo pleno", "Cada 2-4 semanas; casi nada en invierno", "Baja", "10-35 °C",
        "Arenoso y drenante con composta", "Composta 1-2 veces al año",
        ("Terracota grande y pesada", "30 cm o más de diámetro", "Profunda (30 cm o más)", "Orificios amplios y grava", "Cada 2-3 años"),
        ["pudrición por exceso de agua", "cochinilla del carmín", "tallo alargado por falta de luz"],
        "Espinas y gloquidios; la penca y la tuna son comestibles"),
    # Perfiles generales: se usan cuando la especie no está en la base de datos
    _pl("g_follaje", "Planta de follaje tropical (perfil general)", [], "", ["tropical de follaje", "trepadora", "árbol", "palmera"],
        "Luz brillante indirecta", "Cuando 2-4 cm superiores estén secos", "50-60 %", "18-27 °C",
        "Universal con perlita y fibra de coco", "Mensual en primavera-verano",
        ("Cerámica o plástico", "3-5 cm más ancha que el cepellón", "Proporcional al tamaño de la planta", "Orificios y capa de grava", "Cada 2 años"),
        ["exceso de riego", "aire seco", "cochinilla", "ácaros"], "Verifica la toxicidad de la especie si hay mascotas", True),
    _pl("g_suculenta", "Suculenta o cactus (perfil general)", [], "", ["suculenta", "cactus"],
        "Sol directo o luz muy brillante", "Riega y deja secar por completo (cada 10-21 días)", "Baja", "10-30 °C; sin heladas",
        "Muy drenante para cactus (50 % mineral)", "1-2 veces al año, diluido",
        ("Terracota", "Ajustada, 2 cm más ancha que la planta", "Poco profunda", "Orificios amplios", "Cada 2 años"),
        ["pudrición por exceso de agua", "tallo alargado por poca luz", "cochinilla"], "", True),
    _pl("g_hierba", "Hierba aromática u hortaliza (perfil general)", [], "", ["hierba aromática", "hortaliza"],
        "Sol directo 6 h o más", "Sustrato húmedo sin encharcar", "Media", "15-30 °C",
        "Rico en materia orgánica y drenante", "Abono orgánico cada 2-3 semanas",
        ("Terracota o plástico", "20-30 cm de diámetro", "20 cm o más (hortalizas: 30-40 cm)", "Orificios de drenaje", "Se renueva cada ciclo"),
        ["mildiú y hongos por humedad", "pulgones", "mosca blanca"], "", True),
    _pl("g_flor", "Planta de flor o arbusto (perfil general)", [], "", ["planta de flor", "arbusto ornamental", "orquídea", "frutal"],
        "Luz brillante; 4-6 h de sol directo suave", "Cuando 2-3 cm estén secos", "Media", "15-28 °C",
        "Rico en materia orgánica y drenante", "Abono para floración cada 2-3 semanas en temporada",
        ("Cerámica o plástico", "4-5 cm más ancha que el cepellón", "Profunda (25 cm o más)", "Orificios y capa de grava", "Cada 1-2 años"),
        ["botrytis y hongos por exceso de humedad", "pulgones", "caída de botones por riego irregular"], "", True),
]


# ══════════════════════════════════════════════════════════════════════════════
#  IDENTIFICACIÓN DE ESPECIES: validación taxonómica (GBIF) y Pl@ntNet opcional  (v3.9)
#
#  La IA de visión a veces inventa o escribe mal el nombre científico y la familia. Dos apoyos gratuitos corrigen eso:
#   · GBIF Species API (api.gbif.org, sin clave): la taxonomía de referencia mundial. Si el nombre existe (o es un sinónimo
#     o una errata clara) se usa el nombre aceptado y la familia correcta; si no existe, se avisa y no se inventa nada.
#   · Pl@ntNet (my-api.plantnet.org): clasificador de plantas entrenado con millones de fotos de botánicos. Gratis hasta
#     500 identificaciones al día con una clave personal (variable PLANTNET_API_KEY). Sin clave simplemente no se usa.
# ══════════════════════════════════════════════════════════════════════════════
_GBIF_URL = "https://api.gbif.org/v1/species/match"
_GBIF_CACHE = {}
_GBIF_OFF_UNTIL = [0.0]
_PLANTNET_URL = "https://my-api.plantnet.org/v2/identify/all"
_PLANTNET_MIN = 0.45           # puntaje mínimo de Pl@ntNet para fiarse de su especie


def _binomial(name: str) -> str:
    """'Monstera deliciosa Liebm.' → 'Monstera deliciosa'; 'Ficus' → 'Ficus'; 'x' y 'sp.' se ignoran."""
    toks = [t for t in re.split(r"\s+", str(name or "").strip()) if t]
    toks = [t for t in toks if t.lower().strip(".") not in ("sp", "spp", "cf", "aff", "cv")]
    if not toks or not re.match(r"^[A-Za-zÀ-ÿ×-]{2,}$", toks[0]):
        return ""
    out = [toks[0].capitalize()]
    if len(toks) > 1 and re.match(r"^[a-zà-ÿ-]{2,}$", toks[1]):
        out.append(toks[1].lower())
    return " ".join(out)


def gbif_match(name: str):
    """Valida un nombre científico en la taxonomía de GBIF. → dict o None (sin respuesta). {'found': False} si no existe."""
    q = _binomial(name)
    if not q or time.time() < _GBIF_OFF_UNTIL[0]:
        return None
    with _SG_LOCK:
        hit = _GBIF_CACHE.get(q)
    if hit and time.time() - hit[0] < 86400:
        return hit[1]
    try:
        resp = _req.get(_GBIF_URL, headers=_CLIM_UA, params={"name": q, "kingdom": "Plantae"}, timeout=(2.5, 4))
        if resp.status_code != 200:
            raise _SoilProviderError(f"HTTP {resp.status_code}")
        j = resp.json()
    except Exception as ex:
        _GBIF_OFF_UNTIL[0] = time.time() + 120                     # GBIF no responde: 2 min sin insistir
        print(f"[GBIF] {type(ex).__name__}: {str(ex)[:100]}")
        return None
    mt, conf = str(j.get("matchType") or "NONE").upper(), j.get("confidence")
    ok = (str(j.get("kingdom") or "") == "Plantae" and mt in ("EXACT", "FUZZY", "HIGHERRANK") and isinstance(conf, (int, float)) and conf >= 80)
    if not ok:
        out = {"found": False}
    else:
        status = str(j.get("status") or "").upper()
        rank = str(j.get("rank") or "").upper()
        canon = j.get("canonicalName") or ""
        accepted = canon
        if status == "SYNONYM" and j.get("species") and rank in ("SPECIES", "SUBSPECIES", "VARIETY", "FORM"):
            accepted = j["species"]                                # la clasificación de un sinónimo es la del nombre aceptado
        out = {"found": True, "matchType": mt, "confidence": int(conf), "status": status, "rank": rank, "key": j.get("usageKey"),
               "canonical": canon, "accepted": accepted, "family": j.get("family") or "", "genus": j.get("genus") or ""}
    with _SG_LOCK:
        if len(_GBIF_CACHE) > 2000:
            _GBIF_CACHE.clear()
        _GBIF_CACHE[q] = (time.time(), out)
    return out


def gbif_enrich(ident: dict) -> dict:
    """Corrige nombre científico y familia con GBIF. Añade ident['taxonomy'] con lo que se verificó (o que no se pudo)."""
    if not ident.get("isPlant") or not ident.get("scientificName"):
        return ident
    reported = ident["scientificName"]
    g = gbif_match(reported)
    if g is None:
        return ident                                               # GBIF no respondió: se deja tal cual, sin marcar nada
    if not g["found"]:
        ident["taxonomy"] = {"verified": False, "source": "GBIF", "reported": reported}
        return ident
    tax = {"verified": True, "source": "GBIF", "status": g["status"], "matchType": g["matchType"], "key": g["key"], "reported": reported}
    # EXACT/FUZZY a nivel de especie o género: el nombre aceptado manda. HIGHERRANK = la especie no existe en GBIF y solo
    # se confirmó el género: se conserva lo que dijo la IA (puede ser un híbrido o cultivar) y solo se corrige la familia.
    if g["matchType"] in ("EXACT", "FUZZY") and g["accepted"]:
        if _binomial(reported).lower() != _binomial(g["accepted"]).lower():
            tax["corrected"] = True
            print(f"[GBIF] «{reported}» → «{g['accepted']}» ({g['status'].lower()}, {g['matchType'].lower()})")
        ident["scientificName"] = g["accepted"]
    else:
        tax["level"] = "género"
    if g["family"]:
        if ident.get("family") and _norm(ident["family"]) != _norm(g["family"]):
            print(f"[GBIF] familia corregida: {ident['family']} → {g['family']}")
        ident["family"] = g["family"]
    ident["taxonomy"] = tax
    return ident


def _plantnet_enabled() -> bool:
    return bool(os.environ.get("PLANTNET_API_KEY", "").strip())


def _image_bytes(image_b64: str):
    """(bytes, tipo MIME) de una imagen en base64 (con o sin prefijo data:). None si no es JPEG/PNG/WEBP."""
    raw = str(image_b64 or "")
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=False)
    except Exception:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return data, "image/webp"
    return None


def plantnet_identify(image_b64: str):
    """Mejores especies según Pl@ntNet → [{'name','family','score','common'}] (del más al menos probable) o None."""
    key = os.environ.get("PLANTNET_API_KEY", "").strip()
    img = _image_bytes(image_b64) if key else None
    if not key or not img or len(img[0]) > 4_500_000:
        return None
    try:
        resp = _req.post(_PLANTNET_URL, params={"api-key": key, "lang": "es", "nb-results": 3, "include-related-images": "false"},
                         files=[("images", ("planta", img[0], img[1]))], data={"organs": "auto"}, timeout=(4, 18))
        if resp.status_code != 200:
            print(f"[Pl@ntNet] HTTP {resp.status_code} (clave inválida, límite diario o foto sin planta reconocible)")
            return None
        res = resp.json().get("results") or []
    except Exception as ex:
        print(f"[Pl@ntNet] {type(ex).__name__}: {str(ex)[:100]}")
        return None
    out = []
    for r in res[:3]:
        sp = (r or {}).get("species") or {}
        name = sp.get("scientificNameWithoutAuthor") or ""
        score = (r or {}).get("score")
        if name and isinstance(score, (int, float)):
            fam = (sp.get("family") or {}).get("scientificNameWithoutAuthor") or ""
            commons = [c for c in (sp.get("commonNames") or []) if isinstance(c, str) and c.strip()]
            out.append({"name": name, "family": fam, "score": float(score), "common": commons[0].strip() if commons else ""})
    return out or None


def merge_plantnet(ident: dict, pn) -> dict:
    """Combina la identificación de la IA con la de Pl@ntNet. Si no coinciden y Pl@ntNet está seguro, manda Pl@ntNet."""
    if not pn or not ident.get("isPlant"):
        return ident
    top = pn[0]
    info = {"engine": "Pl@ntNet", "score": round(top["score"], 2), "species": top["name"],
            "alternatives": [{"name": a["name"], "score": round(a["score"], 2)} for a in pn[1:]]}
    ai_gen = _binomial(ident.get("scientificName")).split(" ")[0].lower()
    pn_gen = top["name"].split(" ")[0].lower()
    ai_bin, pn_bin = _binomial(ident.get("scientificName")).lower(), _binomial(top["name"]).lower()
    if top["score"] >= _PLANTNET_MIN:
        if ai_bin == pn_bin:
            info["agrees"] = "especie"
        elif ai_gen and ai_gen == pn_gen:
            info["agrees"] = "género"
            ident["scientificName"] = top["name"]                  # mismo género: Pl@ntNet precisa la especie
        else:
            info["agrees"] = "no"
            print(f"[Pl@ntNet] discrepa de la IA: «{ident.get('scientificName')}» vs «{top['name']}» ({top['score']:.2f}); se usa Pl@ntNet")
            ident["scientificName"] = top["name"]
            if top["family"]:
                ident["family"] = top["family"]
            if top["common"]:
                ident["commonName"] = top["common"]
        if info["agrees"] != "no":
            ident["confidence"] = max(_int(ident.get("confidence"), default=0) or 0, min(99, round(top["score"] * 100)))
        else:
            ident["confidence"] = min(99, round(top["score"] * 100))
        if not ident.get("commonName") and top["common"]:
            ident["commonName"] = top["common"]
    else:
        info["agrees"] = "sin certeza"
    ident["identification"] = info
    return ident


def enrich_plant_ident(ident: dict, plantnet=None) -> dict:
    """Pl@ntNet (si hay clave y foto) → validación GBIF. Nunca lanza: ante cualquier fallo devuelve la identificación original."""
    try:
        ident = merge_plantnet(ident, plantnet)
        return gbif_enrich(ident)
    except Exception as ex:
        print(f"[Especies] no se pudo validar: {type(ex).__name__}: {ex}")
        return ident


def _norm(t):
    return unicodedata.normalize("NFD", str(t or "").lower()).encode("ascii", "ignore").decode()


_LEVEL_ES = {"species": "especie exacta", "common": "mismo nombre común", "genus": "mismo género",
             "family": "misma familia", "type": "perfil general por tipo de planta"}


def _phrase_in(key: str, text: str) -> bool:
    """¿Aparece `key` en `text` como palabra o frase completa? (sin que «cinta» coincida con «cintas»)."""
    return bool(key) and re.search(r"(?<![a-z0-9])" + re.escape(key) + r"(?![a-z0-9])", text) is not None


def match_plant_profiles(ident, top_n=2):
    """
    Afinidad (0-100) de cada perfil de PLANT_DB con la identificación. Se distingue QUÉ coincide:
      especie exacta 100 · nombre común 60-80 · mismo género 65 · +15 misma familia · +10 mismo tipo (perfil específico)
      perfil general: 35 si coincide el tipo de planta. Con menos de 25 no se considera coincidencia.
    """
    sci = _norm(_binomial(ident.get("scientificName")))
    genus = sci.split(" ")[0] if sci else ""
    com = _norm(ident.get("commonName"))
    fam, typ = _norm(ident.get("family")), _norm(ident.get("plantType"))
    scored = []
    for p in PLANT_DB:
        keys = [_norm(k) for k in p["keys"] if k]
        name, level = 0, None
        if sci and sci in keys:
            name, level = 100, "species"
        else:
            hit = [k for k in keys if _phrase_in(k, com)]
            if hit:
                name, level = (80 if max(len(k) for k in hit) >= 6 else 60), "common"
            if genus and any(k.split(" ")[0] == genus for k in keys) and 65 > name:
                name, level = 65, "genus"
        score = name
        if fam and p["family"] and _norm(p["family"]) == fam:
            score += 15
            level = level or "family"
        if typ and any(_norm(t) in typ or typ in _norm(t) for t in p["types"]):
            score += 35 if p["generic"] else 10
            level = level or ("type" if p["generic"] else "family" if score >= 25 else None)
        if score >= 25 and level:
            scored.append((min(100, score), 0 if p["generic"] else 1, level, p))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    out = [{"score": sc, "profile": pr, "level": lv} for sc, _, lv, pr in scored[:top_n]]
    if out and out[0]["score"] >= 85:
        out = [m for m in out if m["score"] >= 40]            # con una coincidencia clara, no se añaden perfiles sin relación
    return out


def plant_db_context(matches):
    if not matches:
        return "BASE DE DATOS DE PLANTAS: sin perfil exacto para esta especie."
    lines = ["REFERENCIA DE LA BASE DE DATOS DE PLANTAS (si la coincidencia no es de especie exacta, úsala como guía aproximada "
             "y corrígela con lo que sabes de la especie identificada):"]
    for m in matches:
        p = m["profile"]
        lines.append(
            f"- {p['name']} ({'perfil general' if p['generic'] else p['family']}; coincidencia: {_LEVEL_ES.get(m.get('level'), 'parcial')}, "
            f"afinidad {min(100, m['score'])}/100): "
            f"luz: {p['light']}; riego: {p['water']}; humedad: {p['humidity']}; temperatura: {p['temp']}; "
            f"sustrato: {p['substrate']}; abono: {p['fertilizer']}; maceta: {'; '.join(p['pot'].values())}; "
            f"problemas comunes: {', '.join(p['issues'])}; {p['toxic']}"
        )
    return "\n".join(lines)


def report_from_db(p):
    """Respaldo: informe armado solo con la base de datos cuando la IA de texto no responde."""
    pot = p["pot"]
    return {
        "summary": f"Informe basado en la base de datos de plantas ({p['name']}).",
        "careNeeds": {"light": p["light"], "watering": p["water"], "humidity": p["humidity"], "temperature": p["temp"],
                      "substrate": p["substrate"], "fertilizing": p["fertilizer"],
                      "pruning": "Retira hojas secas o dañadas con tijeras limpias."},
        "potRecommendation": {"material": pot["material"], "size": pot["size"], "depth": pot["depth"],
                              "drainage": pot["drainage"], "substrate": p["substrate"], "repotting": pot["repot"],
                              "currentPotAssessment": "", "extraTips": []},
        "carePlan": {"thisWeek": [], "thisMonth": [], "ongoing": []},
        "warnings": [p["toxic"]] if p["toxic"] else [],
    }


@app.route("/api/scan-plant", methods=["POST"])
def scan_plant():
    """Etapa 1: IA con visión valida que sea una planta, la identifica y describe manchas, formas y colores."""
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        body      = _json_body()
        image_b64 = body.get("image", "")
        lang      = str(body.get("lang", "es"))[:10]
        region    = str(body.get("region", ""))[:120]
        if not image_b64:
            return jsonify({"error": "No se recibió imagen"}), 400
        loc = f" Ubicación del usuario: '{region}'." if region else ""
        prompt = f"""Eres botánico, taxónomo y fitopatólogo experto. Analiza esta foto (responde en '{lang}').{loc}

Rechaza (isPlant:false) fotos donde una planta viva no sea el sujeto principal: personas, animales, objetos, solo suelo, flores cortadas en jarrón, plantas artificiales o superficies.
Responde ÚNICAMENTE con JSON válido, sin texto ni markdown fuera del JSON.

Si NO es una planta:
{{"isPlant":false,"confidence":95,"rejection_reason":"por qué no es una planta analizable"}}

Si SÍ es una planta:
{{
  "isPlant": true,
  "confidence": 85,
  "commonName": "nombre común",
  "scientificName": "género y especie (solo el género si no hay certeza)",
  "family": "familia botánica",
  "plantType": "tropical de follaje|suculenta|cactus|helecho|palmera|orquídea|hierba aromática|hortaliza|frutal|arbusto ornamental|planta de flor|árbol|trepadora|otra",
  "lifeCycle": "perenne|anual|bianual",
  "origin": "región de origen",
  "habitat": "interior|exterior|ambos",
  "description": "rasgos visibles que permitieron identificarla",
  "isPotted": true,
  "potObserved": "material, tamaño aproximado y drenaje de la maceta visible, o 'No visible'",
  "quickCare": ["recomendación de cuidado 1", "recomendación 2", "recomendación 3"],
  "observations": {{
    "overall": "vigor, porte y turgencia general",
    "leaves": "color, forma y textura de las hojas; manchas (color, forma, bordes, halo, tamaño, distribución), decoloraciones, puntas secas, deformaciones",
    "stemsAndBase": "estado del tallo, la base y las raíces visibles",
    "substrate": "humedad, moho o costras del sustrato visible, o 'No visible'",
    "pestsSeen": "insectos, huevos, telarañas, melaza o polvo visibles, o 'ninguno'",
    "spots": [{{"area":"hojas|tallo|flores|frutos|base","color":"color","shape":"forma y bordes","pattern":"patrón o distribución"}}]
  }}
}}
Describe solo lo que realmente se ve; si no hay manchas ni daños, deja "spots" vacío.""" + _COMPACT_JSON
        # 1600 tokens de siempre; si la cuenta de Groq limita la salida por minuto, se ajusta solo (ver _groq_request)
        pn_pool = pn_fut = None
        if _plantnet_enabled():                                 # Pl@ntNet identifica la foto A LA VEZ que la IA de visión
            pn_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="plantnet")
            pn_fut = pn_pool.submit(plantnet_identify, image_b64)
        try:
            ident, _ = call_groq_vision_json(prompt, image_b64, _PLANT_ID_MODELS, 1600, min_tokens=600, tag="ScanPlant")
            ident = normalize_plant_ident(ident)
            pn = None
            if pn_fut is not None and ident.get("isPlant"):
                try:
                    pn = pn_fut.result(timeout=20)
                except Exception as ex:
                    print(f"[Pl@ntNet] sin resultado a tiempo: {type(ex).__name__}")
        finally:
            if pn_pool is not None:
                pn_pool.shutdown(wait=False, cancel_futures=True)
        return jsonify(enrich_plant_ident(ident, pn))
    except Exception as e:
        return _ai_error(e, "ScanPlant")


@app.route("/api/plant-form-identify", methods=["POST"])
def plant_form_identify():
    """Formulario de plantas, etapa 1: identifica la planta a partir del nombre o la descripción escritos (solo texto)."""
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        body  = _json_body()
        lang  = str(body.get("lang", "es"))[:10]
        name  = str(body.get("name", ""))[:80].strip()
        desc  = str(body.get("desc", ""))[:200].strip()
        place = str(body.get("place", ""))[:40]
        if not name:
            return jsonify({"error": "Escribe el nombre de la planta o descríbela"}), 400
        prompt = f"""Eres botánico y taxónomo experto (responde en '{lang}').
El usuario escribió esto sobre su planta. Nombre o descripción: "{name}". Detalles: "{desc or 'ninguno'}". Ubicación: {place or 'no indicada'}.
Identifica la planta más probable; si no hay certeza, elige la más probable y baja "confidence".
Responde ÚNICAMENTE con JSON válido, sin texto ni markdown fuera del JSON:
{{
  "isPlant": true,
  "confidence": 80,
  "commonName": "nombre común",
  "scientificName": "género y especie (solo el género si no hay certeza)",
  "family": "familia botánica",
  "plantType": "tropical de follaje|suculenta|cactus|helecho|palmera|orquídea|hierba aromática|hortaliza|frutal|arbusto ornamental|planta de flor|árbol|trepadora|otra",
  "lifeCycle": "perenne|anual|bianual",
  "origin": "región de origen",
  "habitat": "interior|exterior|ambos",
  "description": "rasgos que distinguen a esta planta",
  "quickCare": ["recomendación de cuidado 1", "recomendación 2", "recomendación 3"]
}}"""
        data = call_groq_json(prompt, max_tokens=1200, temperature=0.3, json_mode=True, effort="low", models=_PLANT_HEALTH_MODELS, min_tokens=700)
        if not data:
            return jsonify({"error": AI_CONN_ERROR, "code": "ai_unavailable"}), 503
        data["isPlant"] = True
        ident = normalize_plant_ident(data)
        if not ident.get("commonName") and not ident.get("scientificName"):
            ident["commonName"] = name[:80]                 # la IA no identificó nada: se conserva lo que escribió el usuario
        return jsonify(enrich_plant_ident(ident))
    except Exception as e:
        return _ai_error(e, "PlantForm")


@app.route("/api/scan-plant-health", methods=["POST"])
def scan_plant_health():
    """Etapa 2: segunda IA (solo texto, sin reenviar la foto) diagnostica a partir de lo que la visión describió."""
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        body  = _json_body()
        lang  = str(body.get("lang", "es"))[:10]
        src   = body.get("ident") if isinstance(body.get("ident"), dict) else {}
        ident = _clean_fields(src, _PLANT_FIELDS)
        obs   = _clean_obs(src.get("observations"))
        spots = "; ".join(" / ".join(f"{k}: {v}" for k, v in sp.items()) for sp in obs.get("spots", [])) or "no se describen manchas"
        intro = ("El usuario describió su planta y sus síntomas en un formulario (no hay foto)."
                 if body.get("source") == "form" else "Una IA de visión analizó la foto de una planta y describió lo que ve.")
        prompt = f"""Eres fitopatólogo y entomólogo experto en diagnóstico por síntomas visuales (responde en '{lang}').
{intro} Diagnostica a partir de esa descripción: relaciona color, forma, bordes, halo, patrón y distribución de las manchas con enfermedades, plagas, deficiencias nutricionales o estrés hídrico/lumínico. No inventes: si la descripción no muestra daños, la planta está sana y "problems" va vacío.

PLANTA: {ident.get('commonName', '?')} ({ident.get('scientificName', '?')}), familia {ident.get('family', '?')}, tipo {ident.get('plantType', '?')}.
ASPECTO GENERAL: {obs.get('overall', '?')}
HOJAS: {obs.get('leaves', '?')}
TALLO Y BASE: {obs.get('stemsAndBase', '?')}
SUSTRATO: {obs.get('substrate', '?')}
PLAGAS A SIMPLE VISTA: {obs.get('pestsSeen', '?')}
MANCHAS Y SÍNTOMAS: {spots}

Responde ÚNICAMENTE con JSON válido, sin texto ni markdown fuera del JSON:
{{
  "healthStatus": "sana|estrés leve|enferma|crítica",
  "healthScore": 80,
  "summary": "estado general de la planta en 2 oraciones",
  "problems": [{{"name":"enfermedad, plaga o deficiencia probable","type":"hongo|bacteria|virus|plaga|deficiencia|estrés hídrico|estrés lumínico|daño físico","probability":70,"severity":"leve|moderada|grave","evidence":"qué manchas, formas o colores lo indican","treatment":["paso 1","paso 2","paso 3"],"prevention":"cómo evitarlo"}}],
  "urgentActions": ["acción inmediata, si la hay"]
}}
Máximo 4 problemas, del más al menos probable."""
        data = call_groq_json(prompt, max_tokens=2200, temperature=0.3, json_mode=True, effort="low", models=_PLANT_HEALTH_MODELS, min_tokens=1500)
        if not data:
            return jsonify({"error": AI_CONN_ERROR, "code": "ai_unavailable"}), 503
        return jsonify(normalize_plant_health(data))
    except Exception as e:
        return _ai_error(e, "ScanPlantHealth")


@app.route("/api/scan-plant-report", methods=["POST"])
def scan_plant_report():
    """Etapa 3: consulta la base de datos de plantas y genera el informe de cuidados y de maceta."""
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        body   = _json_body()
        lang   = str(body.get("lang", "es"))[:10]
        ident  = _clean_fields(body.get("ident"), _PLANT_FIELDS)
        health = body.get("health") if isinstance(body.get("health"), dict) else {}
        h      = _clean_fields(health, {"healthStatus", "healthScore"}, 40)
        probs  = []
        plist  = health.get("problems") if isinstance(health.get("problems"), list) else []
        for p in plist[:4]:
            c = _clean_fields(p, {"name", "severity"}, 80)
            if c.get("name"):
                probs.append(f"{c['name']} ({c.get('severity', '?')})")

        matches = match_plant_profiles(ident)
        prompt = f"""Eres botánico y horticultor experto. Redacta, en '{lang}', el informe de cuidados y de maceta de esta planta.

PLANTA: {ident.get('commonName', '?')} ({ident.get('scientificName', '?')}), familia {ident.get('family', '?')}, tipo {ident.get('plantType', '?')}.
MACETA OBSERVADA: {ident.get('potObserved', 'No visible')}
SALUD: {h.get('healthStatus', '?')} ({h.get('healthScore', '?')}/100). Problemas: {'; '.join(probs) or 'ninguno visible'}.

{plant_db_context(matches)}

Toma la base de datos como referencia principal y adáptala al estado real de esta planta: si hay problemas, ajusta riego, luz y maceta a su tratamiento. Si no hay perfil exacto, razona por familia y tipo.
Responde ÚNICAMENTE con JSON válido, sin texto ni markdown fuera del JSON:
{{
  "summary": "resumen del estado y qué hacer primero, en 2-3 oraciones",
  "careNeeds": {{"light":"","watering":"","humidity":"","temperature":"","substrate":"","fertilizing":"","pruning":""}},
  "potRecommendation": {{"material":"","size":"diámetro y altura recomendados","depth":"","drainage":"","substrate":"","repotting":"cuándo y cómo","currentPotAssessment":"si la maceta observada es adecuada y qué cambiar","extraTips":["",""]}},
  "carePlan": {{"thisWeek":["",""],"thisMonth":["",""],"ongoing":["",""]}},
  "warnings": ["toxicidad para mascotas o niños, si aplica"]
}}"""
        report = {}
        try:
            report = call_groq_json(prompt, max_tokens=2600, temperature=0.4, json_mode=True, effort="low", min_tokens=1800) or {}
        except json.JSONDecodeError as e:
            print(f"[ScanPlantReport] JSON inválido, se usará la base de datos: {e}")
        if report:
            report = normalize_plant_report(report)
            if not (report["summary"] or report["careNeeds"]["watering"] or report["careNeeds"]["light"]):
                report = {}                                     # la IA respondió, pero sin contenido útil
        if not report and matches:
            report = report_from_db(matches[0]["profile"])        # respaldo: informe armado solo con la base de datos
        if not report:
            return jsonify({"error": AI_CONN_ERROR, "code": "ai_unavailable"}), 503
        return jsonify({
            "report": report,
            "databaseMatches": [
                {"name": m["profile"]["name"], "family": m["profile"]["family"], "generic": m["profile"]["generic"],
                 "match_score": min(100, m["score"]), "match_level": _LEVEL_ES.get(m.get("level"), ""),
                 "light": m["profile"]["light"], "water": m["profile"]["water"],
                 "pot": m["profile"]["pot"], "issues": m["profile"]["issues"], "toxic": m["profile"]["toxic"]}
                for m in matches
            ],
        })
    except Exception as e:
        return _ai_error(e, "ScanPlantReport")


# ── PRECARGA DE DATOS DE LA UBICACIÓN ─────────────────────────────────────────────────────────────────────
@app.route("/api/prefetch-profiles", methods=["POST"])
def prefetch_profiles():
    """El cliente la llama en cuanto obtiene la ubicación: WoSIS/SoilGrids tardan, así que se piden desde ya."""
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    body = request.get_json(silent=True) or {}
    lat, lng = _num(body.get("lat"), -90, 90), _num(body.get("lng"), -180, 180)
    if lat is None or lng is None:
        return jsonify({"started": False}), 400
    _prefetch_geodata(lat, lng)
    return jsonify({"started": True})


# ── NOMBRE DEL LUGAR (geocodificación inversa) ─────────────────────────────────────────────────────────────
# Antes la página consultaba Nominatim directamente desde el navegador: sin caché, sin el identificador que exige su política de
# uso y filtrando la IP de cada usuario. Ahora lo hace el servidor: una consulta por kilómetro cuadrado (memoria + base de
# datos, 30 días), como máximo 1 por segundo (norma de Nominatim) y con User-Agent propio (NOMINATIM_EMAIL es opcional).
_GEO_NOM_URL = "https://nominatim.openstreetmap.org/reverse"
_GEO_MEM = {}
_GEO_GATE = threading.Lock()
_GEO_LAST = [0.0]


def reverse_geocode(lat: float, lng: float) -> str:
    """'Ciudad, Estado, País' del punto, o '' si no se pudo (la página muestra entonces las coordenadas)."""
    ck = f"{round(lat, 2)}|{round(lng, 2)}"
    with _SG_LOCK:
        hit = _GEO_MEM.get(ck)
    if hit is not None:
        return hit
    saved = _geo_db_get("soilgrids_cache", f"geo1|{ck}")
    if isinstance(saved, dict) and isinstance(saved.get("r"), str):
        with _SG_LOCK:
            _GEO_MEM[ck] = saved["r"]
        return saved["r"]
    params = {"format": "jsonv2", "lat": round(lat, 5), "lon": round(lng, 5), "zoom": 10, "addressdetails": 1, "accept-language": "es"}
    if os.environ.get("NOMINATIM_EMAIL", "").strip():
        params["email"] = os.environ["NOMINATIM_EMAIL"].strip()
    try:
        with _GEO_GATE:                                            # Nominatim: máximo 1 petición por segundo
            wait = _GEO_LAST[0] + 1.05 - time.time()
            if wait > 0:
                time.sleep(wait)
            _GEO_LAST[0] = time.time()
            resp = _req.get(_GEO_NOM_URL, params=params, headers=_CLIM_UA, timeout=(3, 6))
        if resp.status_code != 200:
            raise _SoilProviderError(f"HTTP {resp.status_code}")
        a = resp.json().get("address") or {}
    except Exception as ex:
        print(f"[Geocodificación] {type(ex).__name__}: {str(ex)[:100]}")
        return ""
    parts = [a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get("county"),
             a.get("state") or a.get("region"), a.get("country")]
    region = ", ".join(dict.fromkeys(str(x).strip() for x in parts if x))[:120]
    if region:                                                     # solo se guarda lo que sirvió (un fallo no se queda 30 días)
        with _SG_LOCK:
            if len(_GEO_MEM) > 2000:
                _GEO_MEM.clear()
            _GEO_MEM[ck] = region
        _geo_db_put("soilgrids_cache", f"geo1|{ck}", {"r": region})
    return region


@app.route("/api/reverse-geocode", methods=["POST"])
def reverse_geocode_route():
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    wait = _rate_limit("geo", session["user_id"], 30, 60)
    if wait:
        return _too_many(wait)
    body = request.get_json(silent=True) or {}
    lat, lng = _num(body.get("lat"), -90, 90), _num(body.get("lng"), -180, 180)
    if lat is None or lng is None:
        return jsonify({"region": ""}), 400
    return jsonify({"region": reverse_geocode(lat, lng)})


# ── ANÁLISIS COMPLETO (Formulario/Foto + SoilGrids + perfiles medidos + mediciones del usuario) ──────────────
@app.route("/api/analyze", methods=["POST"])
def analyze_soil():
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        body   = _json_body()
        data   = body.get("data") if isinstance(body.get("data"), dict) else {}
        lang   = body.get("lang", "es")
        region = data.get("region") or "No especificada"
        lat    = _num(data.get("latitude"), -90, 90)
        lng    = _num(data.get("longitude"), -180, 180)
        has_gps = lat is not None and lng is not None

        loc = f"Región: {region}"
        if has_gps:
            loc += f" | GPS: {lat:.5f}, {lng:.5f} — determina el bioma exacto"

        # Datos con procedencia: modelo SoilGrids (si hay GPS) + perfil medido más cercano + lo que midió el usuario
        sg_data, clim = None, None
        cands, providers = [], {}
        if has_gps:
            # Los datos satelitales, los perfiles medidos (locales + WoSIS) y el clima del sitio se consultan A LA VEZ: la
            # espera es la del más lento, no la suma. Cada uno tiene su propio tiempo máximo y, si falla, el análisis sigue.
            with ThreadPoolExecutor(max_workers=3) as pool:
                f_sg = pool.submit(fetch_soilgrids, lat, lng)
                f_np = pool.submit(nearest_profiles, lat, lng)
                f_cl = pool.submit(fetch_climate, lat, lng)
                try:
                    sg_data = f_sg.result()
                except Exception as ex:
                    print(f"[Analyze] datos satelitales: {ex}")
                try:
                    cands, providers = f_np.result()
                except Exception as ex:
                    print(f"[Analyze] perfiles cercanos: {ex}")
                try:
                    clim = f_cl.result()
                except Exception as ex:
                    print(f"[Analyze] clima: {ex}")

        scan = _clean_fields(body.get("scan"), _SCAN_FIELDS)
        prec = build_precision(data, scan, sg_data, cands, providers, ai_ph_sd=_ai_ph_sd(), calibration=calibration_stats())
        props, tex = prec["properties"], prec.get("texture")

        # Perfiles de referencia de la BD: se comparan con el pH fusionado y, si la textura se midió, con ella
        soil_key = tex["appKey"] if tex and tex["tier"] == 3 else data.get("soilType")
        ref = {**scan, "soilType": soil_key, "estimatedPh": props["ph"]["value"],
               "compaction": data.get("compaction"), "drainage": data.get("drainage")}
        matches = [m for m in match_soil_profile(ref, top_n=2) if m["score"] >= 40]
        vis = "; ".join(f"{lbl}: {scan[k]}" for k, lbl in [
            ("color", "color"), ("texture", "textura"), ("humidity", "humedad"),
            ("organicMatterDesc", "materia orgánica"), ("structureQuality", "estructura")] if scan.get(k))
        ref_ctx = ""
        if vis:
            ref_ctx += f"\nOBSERVACIÓN VISUAL DE LA FOTO (cualitativa, no es una medición): {vis}."
        if matches:
            ref_ctx += "\n" + build_db_context(matches) + "\nUsa estos perfiles solo como referencia regional (no los copies): la tabla de datos verificados manda."
        if clim:
            ref_ctx += "\n\n" + build_climate_context(clim)

        prompt = f"""Eres un edafólogo, agrónomo y fitopatólogo experto. Analiza este suelo en idioma '{lang}'.
Responde SOLO con JSON válido, sin texto adicional ni markdown.

DATOS DEL SUELO:
- {loc}
- Objetivo del usuario: {data.get('goal', 'mejorar suelo')}
- Tipo de suelo (declarado o visto en la foto): {data.get('soilType', 'franco')}
- Historial de incendios: {data.get('fireHistory', 'ninguno')}
- Compactación: {data.get('compaction', 'media')}
- Drenaje: {data.get('drainage', 'moderado')}
- Erosión: {data.get('erosion', 'media')}

{build_precision_context(prec)}
{ref_ctx}

REGLAS CRÍTICAS:
1. Si hay GPS, recomienda EXCLUSIVAMENTE plantas nativas o cultivadas de ESA zona climática y bioma. Si arriba aparece el CLIMA REAL DEL SITIO, úsalo tal cual (altitud, clima Köppen, heladas, temporada de lluvias) en lugar de suponer el clima, y no recomiendes especies que no toleren esas heladas, esa altitud o esa lluvia.
2. Mínimo 4 plantas en cada categoría (reforestación, agricultura, cultivos de cobertura).
3. Si hay incendio reciente, prioriza especies pioneras post-fuego.
4. Adapta las plantas y enmiendas a los valores de la tabla (pH, salinidad, textura, M.O.), no a promedios genéricos.
5. Si conoces la temporada de lluvias y los meses secos del sitio, el calendario de siembra y de riego debe seguirlos.

{{
  "soilStatus": {{
    "phAnalysis": "análisis del pH de la tabla y sus consecuencias en la disponibilidad de nutrientes (menciona la procedencia del dato)",
    "soilHealth": "diagnóstico general del suelo en 2-3 oraciones",
    "mainIssues": ["problema 1", "problema 2", "problema 3"],
    "salinity": {{
      "level": "baja|media|alta|muy alta",
      "impact": "cómo afecta cultivos y flora (sin inventar cifras de dS/m si no hay medición)",
      "recommendation": "acción recomendada"
    }},
    "nutrients": {{
      "nitrogen": "deficiente|adecuado|exceso|sin dato",
      "phosphorus": "deficiente|adecuado|exceso|sin dato",
      "potassium": "deficiente|adecuado|exceso|sin dato",
      "organicMatter": "bajo|medio|alto",
      "analysis": "diagnóstico nutricional: lo medido con su valor y lo no medido en términos cualitativos"
    }},
    "measurementAdvice": ["qué medir primero y cómo hacerlo para mejorar la precisión"]
  }},
  "pests": {{
    "riskLevel": "bajo|medio|alto|crítico",
    "commonPests": [
      {{"name":"nombre","type":"insecto|hongo|bacteria|nematodo","description":"descripción","control":"método de control"}}
    ],
    "preventionTips": ["consejo 1", "consejo 2"]
  }},
  "suitability": {{
    "level": "Óptimo|Aceptable|Requiere rehabilitación|Crítico",
    "score": 75,
    "message": "resumen de aptitud del suelo para el objetivo del usuario"
  }},
  "rehabilitation": {{
    "immediateActions": ["acción urgente 1", "acción 2", "acción 3"],
    "longTermActions": ["estrategia largo plazo 1", "estrategia 2"],
    "amendments": ["enmienda 1 con dosis y el dato del que depende", "enmienda 2", "enmienda 3"]
  }},
  "plants": {{
    "reforestation": [
      {{"name":"nombre científico (común)","reason":"por qué es idónea","care":"cuidados esenciales","nativeRegion":"región de origen","waterNeeds":"bajo|medio|alto"}}
    ],
    "agriculture": [
      {{"name":"cultivo","reason":"adecuación al suelo/clima","care":"cuidados clave","season":"época de siembra","yield":"rendimiento estimado"}}
    ],
    "coverCrops": [
      {{"name":"cultivo de cobertura","benefit":"beneficio específico","howToUse":"cómo y cuándo incorporarlo"}}
    ]
  }},
  "waterManagement": {{
    "irrigationType": "tipo de riego más eficiente",
    "frequency": "frecuencia recomendada",
    "tips": ["consejo 1", "consejo 2", "consejo 3"]
  }},
  "careRoutine": {{
    "soilCare": ["tarea 1 con detalle", "tarea 2", "tarea 3"],
    "plantCare": ["cuidado 1", "cuidado 2"],
    "schedule": "calendario mensual de actividades"
  }}
}}"""

        result = call_groq_json(prompt, max_tokens=4800, temperature=0.5, json_mode=True, effort="low", min_tokens=3200)
        if not result:
            return jsonify({"error": AI_CONN_ERROR, "code": "ai_unavailable"}), 503

        result = normalize_soil_result(result)        # forma garantizada: la página nunca recibe un texto donde espera una lista
        if _soil_content(result) < 8:                 # respuesta cortada o pobre: una segunda oportunidad, y se queda la más completa
            try:
                again = call_groq_json(prompt, tries=1, max_tokens=4800, temperature=0.3, json_mode=True, effort="low", min_tokens=3200)
                if again:
                    again = normalize_soil_result(again)
                    if _soil_content(again) > _soil_content(result):
                        result = again
            except json.JSONDecodeError as ex:
                print(f"[Analyze] segundo intento ilegible: {ex}")
        ss0 = result["soilStatus"]
        if not (ss0["phAnalysis"] or ss0["soilHealth"] or ss0["mainIssues"] or any(result["plants"].values())):
            raise json.JSONDecodeError("la IA respondió sin contenido útil", "", 0)
        apply_precision_to_result(result, prec)      # el servidor fija clases y «sin medición»; la IA no inventa cifras
        result["precision"] = prec
        if sg_data:
            result["soilGridsData"] = sg_data
        if clim:
            result["climate"] = clim
        result["databaseMatches"] = [
            {"name": m["profile"]["name"], "fao_class": m["profile"]["fao_class"], "match_score": min(100, m["score"])}
            for m in matches
        ]
        if data.get("share") is True:                # solo con consentimiento expreso del usuario
            try:
                result["precision"]["shared"] = persist_contribution(data, scan, prec, lat, lng)
            except Exception as ex:
                print(f"[Analyze] aporte no guardado: {ex}")
        return jsonify(result)

    except json.JSONDecodeError:
        return jsonify({"error": "La IA devolvió una respuesta inválida. Inténtalo de nuevo.", "code": "ai_error"}), 500
    except Exception as e:
        print(f"[Analyze] Error: {e}")
        return jsonify({"error": "No se pudo completar el análisis. Inténtalo de nuevo.", "code": "server_error"}), 500


@app.route("/api/calibration", methods=["GET"])
def calibration():
    """Cuánto se equivoca la IA al estimar el pH desde la foto frente a mediciones reales aportadas por usuarios."""
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    return jsonify(calibration_stats())


# ── CHAT ──────────────────────────────────────────────────────────────────────
@app.route("/api/chat", methods=["POST"])
def chat():
    if "user_id" not in session:
        return jsonify({"error": "No autorizado"}), 401
    try:
        body     = _json_body()
        question = (body.get("question") or "").strip()
        context  = str(body.get("context", ""))[:2400]
        lang     = str(body.get("lang", "es"))
        lang     = lang if re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z]{2,4})?", lang) else "es"
        question = question[:1200]
        if not question:
            return jsonify({"error": "Pregunta vacía"}), 400

        prompt = (
            f"Eres EcoChat, el asistente de IA de EcoScan, experto en edafología, agronomía, botánica y reforestación. "
            f"Si te preguntan tu nombre, responde que eres EcoChat. "
            f"Responde en '{lang}', de forma concisa, cálida y práctica (máx. 200 palabras). "
            f"Respeta la PROCEDENCIA de los datos del contexto: una medición directa pesa más que un modelo satelital y este más que una "
            f"estimación por foto; nunca presentes una estimación como medición, no inventes cifras de lo que figura «sin medición» "
            f"y, si falta un dato que cambiaría tu consejo (pH, conductividad, N-P-K), di cómo medirlo. "
            f"Contexto del diagnóstico: {context}\n"
            f"Pregunta: {question}"
        )
        resp = call_groq(prompt, max_tokens=1800, temperature=0.6, json_mode=False, effort="low", min_tokens=500)
        return jsonify({"answer": resp or AI_CONN_ERROR})
    except Exception as e:
        print(f"[Chat] Error: {e}")
        return jsonify({"error": "El chat no pudo responder. Inténtalo de nuevo.", "code": "server_error"}), 500


# ── VERIFICACIÓN DE MODELOS AL ARRANQUE ──────────────────────────────────────
def check_model_availability():
    """
    Consulta la lista de modelos activos en Groq y avisa por consola si alguno de
    los configurados aquí ya fue descontinuado. Así, la próxima vez que Groq retire
    un modelo, te enteras al iniciar el servidor en vez de por un correo o un fallo
    silencioso del chat/análisis.
    """
    try:
        resp = _req.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            timeout=8,
        )
        if resp.status_code != 200:
            print(f"  ⚠️  No se pudo verificar el estado de los modelos (HTTP {resp.status_code})")
            return
        active_ids = {m["id"] for m in resp.json().get("data", [])}
        configured = set(_GROQ_TEXT_MODELS) | {_VISION_MODEL} | set(_PLANT_ID_MODELS) | set(_PLANT_HEALTH_MODELS)
        missing = configured - active_ids

        if missing:
            print(f"  ⚠️  AVISO: estos modelos ya NO aparecen activos en Groq: {', '.join(sorted(missing))}")
            print("      Revisa https://console.groq.com/docs/deprecations y actualiza la lista en server.py")
        else:
            print(f"  ✅ Modelos verificados y activos: {', '.join(sorted(configured))}")
    except Exception as e:
        print(f"  ⚠️  No se pudo verificar disponibilidad de modelos (sin conexión?): {e}")


# ── ARRANQUE ──────────────────────────────────────────────────────────────────
# init_db() y la verificación de modelos se ejecutan siempre que se importa este
# archivo (tanto con "python server.py" en local, como con Gunicorn en producción,
# que es el servidor WSGI real recomendado para Render — el servidor de desarrollo
# de Flask no está pensado para producción).
init_db()
print("\n  🌱 EcoScan v3.0 — backend listo")
print(f"  📊 Base de datos: {len(SOIL_DB)} perfiles de suelos de referencia")
print("  🌍 Datos satelitales de suelo (requieren GPS del usuario), en este orden:")
for _i, (_n, _ok, _why) in enumerate(_sg_provider_report(), 1):
    print(f"      {_i}. {_SG_LABEL[_n]} — " + ("listo" if _ok else f"⚠️  no disponible: {_why}"))
if GROQ_CONFIGURED:
    threading.Thread(target=check_model_availability, name="model-check", daemon=True).start()
else:
    print("  ⚠️  Falta GROQ_API_KEY: la app abre, pero el análisis con IA no funcionará hasta que la configures.")
    print("      Local: ejecuta iniciar_ecoscan.bat (te la pide una vez) o crea un archivo .env con  GROQ_API_KEY=gsk_...")
    print("      Render: Environment → GROQ_API_KEY")
if IS_PROD and not os.environ.get("SECRET_KEY"):
    print("  ⚠️  Define SECRET_KEY en el entorno de producción.")
print()

if __name__ == "__main__":
    # python server.py --importar-perfiles perfiles.csv  → carga perfiles medidos reales (INEGI, laboratorio, WoSIS…)
    if len(sys.argv) >= 3 and sys.argv[1] == "--importar-perfiles":
        import_profiles_csv(sys.argv[2])
        sys.exit(0)
    # Este bloque SOLO corre con "python server.py" (desarrollo local).
    # En producción, Gunicorn importa "app" directamente y nunca llega aquí.
    print(f"  Disponible en http://localhost:{PORT}/  (acceso desde: {HOST})\n")
    if os.environ.get("RENDER") is None and os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.8, lambda: webbrowser.open(f"http://localhost:{PORT}/")).start()
    # load_dotenv=False: este archivo ya lee .env por su cuenta (arriba); así Flask no imprime el aviso "Install python-dotenv"
    app.run(host=HOST, port=PORT, debug=False, threaded=True, load_dotenv=False)
