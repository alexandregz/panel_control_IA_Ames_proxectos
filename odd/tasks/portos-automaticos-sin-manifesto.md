# Porto automático sen manifesto manual

## Contexto
A solución anterior engadía un manifesto manual `container_ports.json` para "garantir"
a unicidade de portos. Pero o panel é de só lectura (non crea contedores): a garantía
era ilusoria e o manifesto só documentaba. Os servizos novos esixían anotación manual.

## Decisión
- **Eliminar `container_ports.json`** e todo o código de registry (loader, campo
  `registry_violation`, badge de frontend).
- O panel deriva **todo** de `configuration.publishedPorts` (fonte de verdade).
  Un servizo novo amosase coa URL correcta **sen anotar nada**.
- Mantiñense: detección de colisións reais en execución (`port_conflict`) e fallback
  de acceso directo por IP do guest (`guest_url`).
- A unicidade de portos, cando se necesite, é responsabilidade do `container run`
  (scripts de arranque), fóra do ámbito do panel.

## Tarefas
- [x] Eliminar `container_ports.json` e o loader/registry de `app.py`
- [x] Quitar o campo `registry_violation` e o badge do frontend
- [x] Verificar que os portos/URLs se derivan de `publishedPorts` sen manifesto
- [x] Commit do cambio

## Non obxectivos
- Non tocar os scripts de arranque dos proxectos (asignación á creación queda fóra).