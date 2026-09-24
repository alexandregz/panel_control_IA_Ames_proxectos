# Porto host único por proxecto

## Contexto
O panel de xestión de containers amosaba a URL de acceso como `http://<ip-host>:<porto>`.
Cada contedor publica un porto no host (`-p host:guest`). Dous proxectos compartían o mesmo
porto host: `ia-xgl-ames` e `gestdoc_downloader` publicaban ambos `host:8765`.
Mentres non corren á vez é inocuo, pero en execución simultánea só un pode ocupar o porto do
host → URL ambigua.

## Decisións
- **Fonte de verdade do porto**: `container inspect → configuration.publishedPorts`
  (`hostPort`, `containerPort`, `hostAddress`). Máis fiable que parsear argumentos do proceso.
- **Garantía de unicidade (mantible)**: manifesto de portos reservados `container_ports.json`
  no repo do panel. Cada proxecto ten un `hostPort` único reservado.
- **Resolución actual**: `ia-xgl-ames` pasa de publicar `host:8765` a `host:8766`
  (guest segue en `8765`). `gestdoc_downloader` mantén `host:8765`,
  `ia-actas-plenos-ames` mantén `host:8501`.

## Notas de recuperación (incidente de recreación)
- A imaxe correcta é `ia-xgl-ames:18.17.4` (única dispoñible en `container image list`;
  non existe a `18.17.3`). No `container image list` só aparece `18.17.4`.
- A base de datos real (471 MB) vive en `data/db/xgl.sqlite` do dir EXTERIOR
  (`/Users/alex/Development/IA-concello-Ames/ia-xgl-ames/data`). O default do
  `container-run.sh` monta `./data` (dir interior, só marcador baleiro). Recreate
  exitoso con `IA_XGL_DATA_DIR=/Users/alex/Development/IA-concello-Ames/ia-xgl-ames/data`.
- Comando de recreación empregado:
  `IA_XGL_PORT=8766 IA_XGL_DATA_DIR=<dir-exterior-data> ./container-run.sh`

## Tarefas
- [ ] Panel: `publishedPorts` como fonte de verdade (hostPort -> URL)
- [ ] Manifest `container_ports.json` + loader + verificación fronte ao real
- [ ] Detección de colisións entre contedores en execución + exposición de `guest_ip`
- [ ] Frontend: badge de conflito + acceso directo por IP do guest (fallback)
- [x] Recrear `ia-xgl-ames` con host 8766 → DESFEITO e RECUPERADO:
  - 1º intento con `IA_XGL_IMAGE=ia-xgl-ames:18.17.3` FALLOU (a imaxe real é 18.17.4)
    e borrou o contedor. A imaxe persiste en `container image list`.
  - 2º intento (imaxe 18.17.4 por default) arrancou pero parou: default montaba
    `./data` interior (marcador baleiro).
  - 3º intento EXITOSO: `IA_XGL_PORT=8766
    IA_XGL_DATA_DIR=<dir-exterior-data> ./container-run.sh`. Contedor en execución,
    guest 192.168.64.13, host 8766, HTTP 200 en host:8766.
- [ ] Verificar e commit por unidade de traballo

## Non obxectivos (por agora)
- Non cambiar a definición de contedores doutros proxectos (só xgl).
- Non usar IP do guest como URL principal.