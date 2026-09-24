import os
import logging
import socket
import subprocess
from typing import List, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import uvicorn
import json
import re
from typing import List, Dict, Any


# 1. Lemos a variable de contorna 'DEBUG' (será True se lle pasas "1" ou "true")
DEBUG_MODE = os.environ.get("DEBUG", "0").lower() in ("1", "true", "yes")

# 2. Configuramos o nivel do log
log_level = logging.DEBUG if DEBUG_MODE else logging.WARNING

# Configurar o sistema de logs de Python con ese nivel
logging.basicConfig(level=log_level, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Panel Container macOS")


# --- Función para determinar a IP externa/local do Mac ---

def get_external_ip() -> str:
    """Calcula a IP externa (local na LAN) do Mac."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Non precisa conexíón real, só serve para determinar a interface de saída
        s.connect(('1.1.1.1', 1))
        ip = s.getsockname()[0]
    except Exception:
        ip = '127.0.0.1'
    finally:
        s.close()
    return ip


# --- Funcións auxiliares para comunicarse coa CLI 'container' ---
def run_command(cmd: List[str]) -> str:
    """Executa un comando na terminal e devolve a saída estándar con logs detallados."""
    cmd_str = " ".join(cmd)
    
    # 3. Cambiamos info por debug. Así só saen se DEBUG_MODE é True
    logger.debug(f"Executando comando: {cmd_str}") 
    
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True
        )
        logger.debug(f"Éxito [{cmd_str}]")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        error_msg = (e.stderr.strip() or e.stdout.strip() or str(e))
        # Os erros seguimos logueándolos como ERROR para que saian sempre
        logger.error(f"Fallo no comando [{cmd_str}]. Detalles: {error_msg}")
        raise HTTPException(status_code=500, detail=f"Erro interno do CLI: {error_msg}")
    except Exception as e:
        logger.error(f"Erro do sistema ao intentar executar [{cmd_str}]: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro inesperado: {str(e)}")


def get_container_network_info(c_id: str) -> Dict[str, str]:
    """Extrae a info de rede dun contedor desde 'container inspect'.

    Fonte de verdade do porto: configuration.publishedPorts (hostPort). Ademais le
    a IP do guest (status.networks[].ipv4Address), que é única por contedor e serve
    como fallback de acceso directo cando hai unha colisión de porto host.
    """
    info = {"host_port": "", "container_port": "", "guest_ip": ""}
    try:
        res = subprocess.run(
            ["container", "inspect", c_id],
            capture_output=True, text=True
        )
        if res.returncode != 0 or not res.stdout.strip():
            return info

        data = json.loads(res.stdout)
        container = data[0] if isinstance(data, list) and data else {}
        cfg = container.get("configuration", {})
        init = cfg.get("initProcess", {})

        # IP do guest (única por contedor, rede bridge100)
        networks = container.get("status", {}).get("networks", []) or []
        if networks:
            ip = networks[0].get("ipv4Address", "") or ""
            if ip:
                info["guest_ip"] = ip.split("/")[0]

        # Porto publicado no host: fonte de verdade
        published = cfg.get("publishedPorts", []) or []
        if published:
            info["host_port"] = str(published[0].get("hostPort") or "")
            info["container_port"] = str(published[0].get("containerPort") or "")
            return info

        # Fallback: dedución do porto desde os argumentos do proceso (contedores sen -p)
        args = init.get("arguments", []) or []
        port = ""
        for arg in args:
            match = re.match(r"^--(?:server\.)?port=(\d{1,5})$", arg)
            if match:
                port = match.group(1)
                break
        if not port:
            for i, arg in enumerate(args):
                if arg in ("--port", "-p", "--server.port") and i + 1 < len(args) and args[i + 1].isdigit():
                    port = args[i + 1]
                    break
        if not port:
            for env in init.get("environment", []) or []:
                match = re.match(r"^PORT=(\d{1,5})$", env)
                if match:
                    port = match.group(1)
                    break
        info["container_port"] = port
        info["host_port"] = port
    except Exception as e:
        logger.debug(f"Erro no inspect de {c_id}: {e}")
    return info

def get_containers() -> List[Dict[str, Any]]:
    """Obtén a lista de contedores. Se o servizo do sistema está caído, devolve [] sen lanzar erro 500."""
    try:
        output = run_command(["container", "list", "-a"])
        lines = output.splitlines()

        if len(lines) <= 1:
            return []

        host_ip = get_external_ip()
        containers = []

        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 2:
                c_id = parts[0]
                image = parts[1]

                if image.startswith("python") or "builder" in image or c_id == "buildkit":
                    continue

                state = "running" if "running" in line.lower() else "stopped"
                is_running = state == "running"

                net = get_container_network_info(c_id)
                host_port = net["host_port"]
                container_port = net["container_port"]
                guest_ip = net["guest_ip"]

                external_url = f"http://{host_ip}:{host_port}" if (is_running and host_port) else None
                guest_url = f"http://{guest_ip}:{container_port}" if (is_running and guest_ip and container_port) else None

                containers.append({
                    "id": c_id,
                    "name": c_id,
                    "image": image,
                    "state": state,
                    "running": is_running,
                    "url": external_url,
                    "guest_url": guest_url,
                    "port": host_port,
                    "container_port": container_port,
                    "host_port": host_port,
                    "guest_ip": guest_ip,
                    "port_conflict": False,
                    "conflicting_with": [],
                })

        # Detección de colisións de porto host entre contedores en execución
        running_ports = [c["host_port"] for c in containers if c["running"] and c["host_port"]]
        used = {p for p in running_ports if running_ports.count(p) > 1}
        running = [c for c in containers if c["running"]]
        for c in containers:
            if c["running"] and c["host_port"] in used:
                c["port_conflict"] = True
                c["conflicting_with"] = [o["id"] for o in running
                                          if o["id"] != c["id"] and o["host_port"] == c["host_port"]]

        return containers
    except Exception as e:
        logger.error(f"O servizo do sistema parece estar caído ou sen resposta: {e}")
        # Devolvemos lista baleira para que o frontend entenda que non hai contedores/servizo
        return []


# --- Endpoints API REST ---

@app.get("/api/containers")
def list_containers():
    return get_containers()


@app.post("/api/containers/{container_id}/start")
def start_container(container_id: str):
    run_command(["container", "start", container_id])
    return {"status": "ok", "message": f"Contedor {container_id} iniciado"}


@app.post("/api/containers/{container_id}/stop")
def stop_container(container_id: str):
    run_command(["container", "stop", container_id])
    return {"status": "ok", "message": f"Contedor {container_id} detido"}


@app.post("/api/system/stop")
def stop_system_vm():
    run_command(["container", "system", "stop"])
    return {"status": "ok", "message": "Servizo de sistema detido (RAM liberada)"}

@app.post("/api/system/start")
def start_system_vm():
    """Inicia o servizo dimonio de contedores de macOS."""
    run_command(["container", "system", "start"])
    return {"status": "ok", "message": "Servizo do sistema iniciado correctamente"}


# --- Frontend Interactivo (Single Page App) ---

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return """
    <!DOCTYPE html>
    <html lang="gl">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Panel Container macOS</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen font-sans antialiased" x-data="containerApp()" x-init="init()">
        
        <!-- Header -->
        <header class="border-b border-slate-800 bg-slate-950/50 backdrop-blur sticky top-0 z-10">
            <div class="max-w-6xl mx-auto px-6 py-4 flex justify-between items-center">
                <div class="flex items-center gap-3">
                    <div class="p-2 bg-indigo-600/20 text-indigo-400 rounded-lg">
                        <i class="fa-solid fa-cube text-xl"></i>
                    </div>
                    <div>
                        <h1 class="text-lg font-bold text-white leading-tight">macOS Container Manager</h1>
                        <p class="text-xs text-slate-400">Xestión nativa de contedores (Virtualization.framework)</p>
                    </div>
                </div>
                
                <div class="flex items-center gap-3">
                    <button @click="stopSystemVM()" class="text-xs bg-rose-950/60 hover:bg-rose-900 text-rose-300 border border-rose-800/50 px-3 py-2 rounded-lg font-medium transition flex items-center gap-2">
                        <i class="fa-solid fa-power-off"></i>
                        <span>Liberar RAM (Stop System)</span>
                    </button>
                    <button @click="fetchContainers()" class="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition" title="Actualizar">
                        <i class="fa-solid fa-rotate-right" :class="{'fa-spin': loading}"></i>
                    </button>
                </div>
            </div>
        </header>

       <!-- Contido principal -->
        <main class="max-w-6xl mx-auto px-6 py-8">
            
            <!-- Mensaxes de alerta -->
            <div x-show="toast.show" x-transition 
                 :class="toast.type === 'error' ? 'bg-rose-500/10 border-rose-500/20 text-rose-300' : 'bg-emerald-500/10 border-emerald-500/20 text-emerald-300'"
                 class="mb-6 p-4 rounded-xl border flex items-center justify-between">
                <span x-text="toast.message" class="text-sm font-medium"></span>
                <button @click="toast.show = false" class="text-slate-400 hover:text-white">&times;</button>
            </div>

            <!-- Grella de contedores -->
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                
                <!-- Estado sen contedores ou servizo caído -->
                <template x-if="containers.length === 0 && !loading">
                    <div class="col-span-full border border-dashed border-slate-800 rounded-2xl p-12 text-center bg-slate-950/20">
                        <div class="p-4 bg-indigo-600/10 text-indigo-400 rounded-full w-16 h-16 mx-auto mb-4 flex items-center justify-center border border-indigo-500/20">
                            <i class="fa-solid fa-server text-2xl"></i>
                        </div>
                        <h3 class="text-slate-200 font-bold text-lg mb-1">Servizo de contedores sen resposta ou baleiro</h3>
                        <p class="text-xs text-slate-400 max-w-md mx-auto mb-6">
                            Se o servizo do sistema está detido ou fallou a conexión XPC, debes inicializalo de novo antes de poder xestionar os contedores.
                        </p>
                        
                        <div class="flex items-center justify-center gap-3">
                            <button @click="startSystemVM()" :disabled="actionLoading === 'system-start'"
                                    class="bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-900/50 text-white font-medium px-5 py-2.5 rounded-xl text-xs transition flex items-center gap-2 shadow-lg shadow-indigo-600/20">
                                <i class="fa-solid fa-power-off" x-show="actionLoading !== 'system-start'"></i>
                                <i class="fa-solid fa-spinner fa-spin" x-show="actionLoading === 'system-start'"></i>
                                <span>Iniciar Servizo do Sistema (`container system start`)</span>
                            </button>
                            <button @click="fetchContainers()" class="bg-slate-800 hover:bg-slate-700 text-slate-300 px-4 py-2.5 rounded-xl text-xs font-medium transition">
                                <i class="fa-solid fa-rotate-right mr-1"></i>
                                <span>Reintentar</span>
                            </button>
                        </div>
                    </div>
                </template>

                <template x-for="c in containers" :key="c.id">
                    <div class="bg-slate-800/50 border border-slate-700/50 rounded-2xl p-5 flex flex-col justify-between hover:border-slate-600 transition shadow-lg">
                        <div>
                            <div class="flex items-start justify-between gap-2 mb-3">
                                <div>
                                    <h2 class="font-bold text-slate-100 truncate max-w-[180px]" x-text="c.name" :title="c.name"></h2>
                                    <p class="text-xs text-slate-400 font-mono mt-0.5 truncate max-w-[180px]" x-text="c.image" :title="c.image"></p>
                                </div>
                                <span class="px-2.5 py-1 text-xs font-semibold rounded-full border flex items-center gap-1.5"
                                      :class="c.running 
                                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' 
                                        : 'bg-slate-700/50 text-slate-400 border-slate-600/50'">
                                    <span class="w-1.5 h-1.5 rounded-full" :class="c.running ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'"></span>
                                    <span x-text="c.running ? 'Executando' : 'Detido'"></span>
                                </span>
                            </div>

                            <!-- Aviso de conflito de porto host (dous contedores publican o mesmo) -->
                            <template x-if="c.running && c.port_conflict">
                                <div class="mt-3 p-2 bg-rose-950/50 rounded-lg border border-rose-500/40">
                                    <p class="text-[10px] text-rose-300 uppercase tracking-wider font-semibold mb-1">⚠️ Conflito de porto host</p>
                                    <p class="text-xs text-rose-200">
                                        <span x-text="c.id"></span> e <span class="font-mono" x-text="c.conflicting_with.join(', ')"></span>
                                        publican ambos o porto <span class="font-mono" x-text="c.host_port"></span> no host.
                                    </p>
                                    <p class="text-[11px] text-rose-300/80 mt-1">Reasigna un porto host único a un deles ao recrear o contedor.</p>
                                </div>
                            </template>

                            <!-- Link de acceso IP externa (só se mostra se c.url existe e non hai conflito) -->
                            <template x-if="c.running && c.url && !c.port_conflict">
                                <div class="mt-3 p-2 bg-slate-900/60 rounded-lg border border-slate-700/30">
                                    <p class="text-[10px] text-slate-400 uppercase tracking-wider font-semibold mb-0.5">Acceso Externo</p>
                                    <a :href="c.url" target="_blank" rel="noopener noreferrer" class="text-xs font-mono text-indigo-400 hover:text-indigo-300 hover:underline flex items-center gap-1 truncate">
                                        <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                                        <span x-text="c.url"></span>
                                    </a>
                                </div>
                            </template>

                            <!-- Acceso directo por IP do guest (fallback cando hai conflito ou falta URL externa) -->
                            <template x-if="c.running && c.guest_url">
                                <div class="mt-3 p-2 bg-slate-900/40 rounded-lg border border-slate-700/30">
                                    <p class="text-[10px] text-slate-500 uppercase tracking-wider font-semibold mb-0.5">Acceso directo (rede interna do Mac)</p>
                                    <a :href="c.guest_url" target="_blank" rel="noopener noreferrer" class="text-xs font-mono text-cyan-400 hover:text-cyan-300 hover:underline flex items-center gap-1 truncate">
                                        <i class="fa-solid fa-arrow-up-right-from-square text-[10px]"></i>
                                        <span x-text="c.guest_url"></span>
                                    </a>
                                </div>
                            </template>
                        </div>

                        <!-- Botóns de acción -->
                        <div class="mt-6 pt-4 border-t border-slate-700/40 flex items-center gap-2">
                            <template x-if="!c.running">
                                <button @click="startContainer(c.id)" :disabled="actionLoading === c.id"
                                        class="w-full bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-900/50 text-white font-medium py-2 px-4 rounded-xl text-xs transition flex items-center justify-center gap-2">
                                    <i class="fa-solid fa-play text-xs" x-show="actionLoading !== c.id"></i>
                                    <i class="fa-solid fa-spinner fa-spin text-xs" x-show="actionLoading === c.id"></i>
                                    <span>Arrancar</span>
                                </button>
                            </template>

                            <template x-if="c.running">
                                <div class="flex w-full gap-2">
                                    <template x-if="c.url || c.guest_url">
                                        <a :href="c.port_conflict ? c.guest_url : c.url" target="_blank" rel="noopener noreferrer"
                                           class="flex-1 bg-emerald-600 hover:bg-emerald-500 text-white font-medium py-2 px-3 rounded-xl text-xs transition flex items-center justify-center gap-1.5"
                                           :title="c.port_conflict ? 'Acceso directo por IP do guest (porto host en conflito)' : ''">
                                            <i class="fa-solid fa-external-link text-xs"></i>
                                            <span>Abrir App</span>
                                        </a>
                                    </template>
                                    
                                    <button @click="stopContainer(c.id)" :disabled="actionLoading === c.id"
                                            class="bg-slate-700 hover:bg-rose-600/80 disabled:bg-slate-800 text-slate-200 hover:text-white font-medium py-2 px-3 rounded-xl text-xs transition flex items-center justify-center gap-1.5"
                                            :class="c.url ? '' : 'w-full'">
                                        <i class="fa-solid fa-stop text-xs" x-show="actionLoading !== c.id"></i>
                                        <i class="fa-solid fa-spinner fa-spin text-xs" x-show="actionLoading === c.id"></i>
                                        <span>Parar</span>
                                    </button>
                                </div>
                            </template>
                        </div>
                    </div>
                </template>

            </div>
        </main>

        <script>
            function containerApp() {
                return {
                    containers: [],
                    loading: false,
                    actionLoading: null,
                    toast: { show: false, message: '', type: 'success' },

                    async init() {
                        await this.fetchContainers();
                        setInterval(() => this.fetchContainers(true), 5000);
                    },

                    showToast(msg, type = 'success') {
                        this.toast = { show: true, message: msg, type: type };
                        setTimeout(() => this.toast.show = false, 4000);
                    },

                    async fetchContainers(silent = false) {
                        if (!silent) this.loading = true;
                        try {
                            const res = await fetch('/api/containers');
                            if (res.ok) {
                                this.containers = await res.json();
                            } else {
                                // Se o backend devolveu un erro (ex. 500), vaciamos a lista para mostrar a pantalla de arranque do sistema
                                this.containers = [];
                            }
                        } catch (err) {
                            console.error("Erro ao obter contedores:", err);
                            this.containers = [];
                        } finally {
                            this.loading = false;
                        }
                    },

                    async startContainer(id) {
                        this.actionLoading = id;
                        console.log(`[Start] Iniciando petición para o contedor: ${id}`);
                        try {
                            const res = await fetch(`/api/containers/${id}/start`, { method: 'POST' });
                            if (res.ok) {
                                console.log(`[Start] Contedor ${id} arrancado correctamente.`);
                                this.showToast(`Contedor ${id} arrancado correctamente`);
                                await this.fetchContainers(true);
                            } else {
                                // Intenta ler o erro enviado polo backend
                                let errorDetail = `Erro HTTP: ${res.status}`;
                                try {
                                    const err = await res.json();
                                    errorDetail = err.detail || JSON.stringify(err);
                                } catch (parseErr) {
                                    const textInfo = await res.text();
                                    console.error(`[Start] O backend non devolveu JSON válido. Corpo:`, textInfo);
                                    errorDetail = `Erro do servidor: ${res.statusText}`;
                                }
                                console.error(`[Start] Fallo ao arrancar ${id}:`, errorDetail);
                                this.showToast(errorDetail, 'error');
                            }
                        } catch (e) {
                            // Este catch agora só saltará se hai un fallo real de Javascript ou se cae a rede 100%
                            console.error(`[Start] Excepción capturada (rede ou JS):`, e);
                            this.showToast(`Fallo crítico: ${e.message}`, 'error');
                        } finally {
                            this.actionLoading = null;
                        }
                    },

                    async stopContainer(id) {
                        this.actionLoading = id;
                        console.log(`[Stop] Iniciando petición para deter o contedor: ${id}`);
                        try {
                            const res = await fetch(`/api/containers/${id}/stop`, { method: 'POST' });
                            if (res.ok) {
                                console.log(`[Stop] Contedor ${id} detido correctamente.`);
                                this.showToast(`Contedor ${id} detido correctamente`);
                                await this.fetchContainers(true);
                            } else {
                                let errorDetail = `Erro HTTP: ${res.status}`;
                                try {
                                    const err = await res.json();
                                    errorDetail = err.detail || JSON.stringify(err);
                                } catch (parseErr) {
                                    const textInfo = await res.text();
                                    console.error(`[Stop] O backend non devolveu JSON válido. Corpo:`, textInfo);
                                    errorDetail = `Erro do servidor: ${res.statusText}`;
                                }
                                console.error(`[Stop] Fallo ao deter ${id}:`, errorDetail);
                                this.showToast(errorDetail, 'error');
                            }
                        } catch (e) {
                            console.error(`[Stop] Excepción capturada (rede ou JS):`, e);
                            this.showToast(`Fallo crítico: ${e.message}`, 'error');
                        } finally {
                            this.actionLoading = null;
                        }
                    },

                    async stopSystemVM() {
                        if (!confirm("Isto apagará o servizo de contedores no Mac para liberar a memoria RAM. Continuar?")) return;
                        
                        try {
                            const res = await fetch('/api/system/stop', { method: 'POST' });
                            if (res.ok) {
                                this.showToast('Servizo do sistema detido. Memoria RAM liberada.');
                                await this.fetchContainers(true);
                            }
                        } catch (e) {
                            this.showToast('Erro ao deter o servizo do sistema', 'error');
                        }
                    },

                    async startSystemVM() {
                        this.actionLoading = 'system-start';
                        try {
                            const res = await fetch('/api/system/start', { method: 'POST' });
                            if (res.ok) {
                                this.showToast('Servizo do sistema iniciado correctamente');
                                await this.fetchContainers(true);
                            } else {
                                const err = await res.json();
                                this.showToast(err.detail || 'Erro ao iniciar o servizo do sistema', 'error');
                            }
                        } catch (e) {
                            this.showToast('Erro de rede ao iniciar o servizo do sistema', 'error');
                        } finally {
                            this.actionLoading = null;
                        }
                    }
                }
            }
        </script>
    </body>
    </html>
    """


if __name__ == "__main__":
    # Ao executar co comando 'python', entrará por aquí e lanzará o servidor.
    # Podes fixar aquí o porto 8080 e o modo reload que usabas antes na terminal.
    logger.info("Arrincando servidor Uvicorn...")
    uvicorn.run("app:app", host="0.0.0.0", port=8080, reload=True)