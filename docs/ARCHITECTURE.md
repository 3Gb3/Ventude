# Arquitetura

Python 3.12 + PySide6-Essentials (só QtCore, QtGui, QtWidgets, QtSvg e QtNetwork). HTTP, SQLite, DPAPI e registro do Windows vêm da biblioteca padrão. WinRT só é instalado no Windows, para a mídia. O `curl_cffi` (cerca de 2 MB no Windows) só é usado para o claude.ai e o grok.com, que ficam atrás do Cloudflare.

```
src/ventude/
  app.py               inicialização, instância única, bandeja, ilha
  core/
    config.py          Settings (dataclass) e lista de abas
    store.py           SQLite único: ajustes, atalhos, agenda, tarefas, cache, segredos + migração
    net.py             HTTP sobre urllib + browser_request (curl_cffi, "cara de Chrome") para sites com Cloudflare
    tasks.py           pool de threads que entrega o resultado na thread da interface
    secrets.py         DPAPI (CryptProtectData) via ctypes
    timefmt.py         datas e durações em português
  limits/
    model.py           Window, Report, FetchContext, ProviderError
    claude.py          Claude Code (OAuth, renova o token vencido, WSL) e claude.ai (sessionKey ou cURL)
    antigravity.py     descoberta do language server local + RPC de cotas
    grok.py            Grok CLI (créditos) e grok.com (consultas por modo, cookie ou cURL)
    codex.py           Codex CLI (~/.codex/auth.json) ou cURL do chatgpt.com: janelas de 5h e semanal
    cursor.py          login do Cursor (state.vscdb, só leitura) ou cookie do cursor.com: uso do plano
    copilot.py         token da extensão do Copilot ou colado: cotas do mês
    detect.py          quais IAs opcionais já têm login neste PC
    web.py             lê o que é colado do navegador: cookie, cabeçalhos, "Copiar como cURL"
    manager.py         agenda consultas, cache, estado "stale", avisos de 80/95% e renovação
  services/
    media.py           GlobalSystemMediaTransportControls (WinRT, sem polling), só Spotify ou qualquer player
    weather.py         Open-Meteo (agora, 7 dias e hora a hora)
    market.py          Yahoo Finance (chart v8)
    news.py            RSS/Atom
    launcher.py        abrir apps, pastas, arquivos e sites
    system.py          iniciar com o Windows, atalho global, tela cheia, volume, hook da tecla Windows
    timer.py           timer e Pomodoro (QTimer + time.monotonic)
    shelf.py           estante (soltos, histórico da área de transferência, fixados no SQLite)
    ocr.py             OCR do Windows (Windows.Media.Ocr) numa thread de trabalho
    com.py             COM mínimo via ctypes: GUIDs, CoCreateInstance, chamadas pela vtable
    winutil.py         processos, descrição do .exe, atalhos .lnk e foco de janelas
    hardware.py        CPU, GPU (NVML/PDH), RAM, processos, rede (GetIfTable2), ping, disco e bateria
    audio.py           Core Audio: dispositivos, padrão (IPolicyConfig), volumes e mixer por app
    privacy.py         microfone/câmera em uso (CapabilityAccessManager\ConsentStore)
    caffeine.py        SetThreadExecutionState (tela sempre acesa)
    notifications.py   central de notificações do Ventude (avisos guardados no SQLite)
    winnotify.py       notificações dos outros apps (UserNotificationListener do WinRT)
    nightlight.py      Luz noturna liga/desliga pelo estado do CloudStore (HKCU)
    qr.py              gerador de QR Code (ISO 18004) em Python puro
    lyrics.py          letra sincronizada do lrclib.net, com cache
    calc.py            calculadora segura da busca (ast, sem eval)
    indexer.py         índice de apps, de todas as pastas do PC e de mídia/documentos (busca por str.find) + Terminal/editor
    sites.py           sites comuns da busca ("youtube" -> abrir no navegador) e endereços digitados
    power.py           Central de Energia e atualização do Windows pendente
    taskbar.py         esconder/restaurar a barra do Windows (com espaço reservado), janelas e AppUserModelID
    apps.py            catálogo do Shell: categorias, Configurações do Windows, recentes, conta, bandeja
    shellicons.py      ícones reais do Windows (IShellItemImageFactory) numa thread própria
    dwm.py             prévias ao vivo das janelas, vidro (acrylic) e cantos do Windows 11
  ui/
    theme.py           paleta "Brisa" (grafite azulado + gradiente menta → céu), fontes/cores em cache, folha de estilo
    icons.py           ícones SVG de traço, variantes preenchidas das abas, clima em cores e monograma de apps
    motion.py          linguagem de movimento: curvas cubic-bezier (Python e QEasingCurve nativa), mola e durações
    tabs.py            abas da ilha: grupos de páginas irmãs e o indicador que desliza até a aba ativa
    widgets.py         Card, Ring, UsageBar, Sparkline, Switch, CheckCircle, Equalizer...
    brand.py           material Tinta (fundo, fio de luz, brilho "aurora"), tornadinho, sombras e menus
    context.py         AppContext: serviços + ajustes vivos + sinais compartilhados + clima central
    island.py          janela da ilha: pílula (zonas), painel, Controles, avisos, animação, arraste, hover, atalho
    toast.py           avisos na ilha e lembretes da Agenda
    command.py         CommandEngine (motor da busca, usado em todo lugar) + barra de comandos da ilha
    capture.py         sobreposição de tela: conta-gotas e seleção para OCR
    qr.py              cartão do QR Code dentro da ilha
    power_menu.py      menu da Central de Energia na ilha (sem o Shell)
    lockscreen.py      tela de bloqueio do Ventude e bloqueio por inatividade (a senha fica com o Windows)
    blackhole.py       efeito "buraco negro" da ilha (tecla Windows 3 vezes)
    echo.py            Eco: relógio passivo no topo dos outros monitores
    pill_overlay.py    barra de volume desenhada na própria pílula
    tray.py            ícone e menu da bandeja, avisos
    update_dialog.py   pergunta e download da atualização, no visual da ilha
    pages/             uma classe por página, criada só quando é aberta (Ajustes com navegação por seções)
    shell/             Ventude Shell
      controller.py    ShellController: Doca, Eco, Lançador, prévias, hook da tecla Windows, energia e restauração
      dock.py          Doca flutuante (uma por monitor): fixados, apps abertos, Iniciar e a ponta direita
      dock_model.py    modelo puro da Doca: layout, overflow, esquiva e borda de revelação
      items.py         agrupamento das janelas por app, fixados e filtro por monitor
      launcher.py      Lançador: Início (fixados + continuar), busca, Todos os apps, Energia, segundo plano e avisos
      results.py       lista de resultados da busca com verbos e o ponto deslizante
      tiles.py         grades desenhadas à mão (blocos e linhas)
      controls.py      Controles da ilha (Win + A): teclas rápidas e as ferramentas de tela
      notifications.py Notificações do Ventude e do Windows (página do Lançador e sino da Doca)
      background.py    apps em segundo plano (bandeja) como página do Lançador
      thumbs.py        prévias ao vivo das janelas (DWM)
      fly.py           ícone voador do Lançador até a Doca ao fixar
      curtain.py       Interruptor: "Até logo!" antes de desligar/reiniciar/suspender/bloquear e "Bom dia!"
      common.py        janela Tinta com abrir/fechar animado, botões com halo, ícone real (ou monograma), avatar
```

## Ventude Shell

- **Ligar/desligar**: `replace_taskbar` (barra) e `replace_start_menu` (tecla Windows). A primeira abertura da 2.0 (layout 6) liga os dois e tira data e clima da ilha. O `ShellController` só existe no app (os testes passam uma barra do Windows de mentira e uma energia falsa).
- **Espaço da barra**: o `TaskbarHider` esconde `Shell_TrayWnd`/`Shell_SecondaryTrayWnd` e define a área útil de cada monitor como a tela menos a altura da barra do Ventude (48 px × escala do monitor). Com "ocultar automaticamente", a área útil é a tela toda e a barra desce até o mouse encostar embaixo. Em tela cheia (jogos, vídeos), a barra some de vez e não volta ao encostar o mouse embaixo. Tela cheia = janela não maximizada cuja área cliente cobre o monitor (ou sem barra de título cobrindo o monitor), ou Direct3D exclusivo/apresentação pelo `SHQueryUserNotificationState`.
- **Agrupamento**: janela e fixado casam pelo AppUserModelID (`SHGetPropertyStoreForWindow`), pela família do pacote da Loja (`WindowsApps\Nome_versão__editor`) ou pelo .exe (atalhos .lnk resolvidos). Apps da Loja usam o ícone de `shell:AppsFolder\AppID`.
- **Fixados e histórico**: tabelas `pins` (lugar `taskbar`/`start`) e `launches` (Pesquisas recentes) no SQLite. Na primeira abertura, a barra começa com os fixados da barra do Windows e o Iniciar com apps conhecidos que estiverem instalados.
- **Tecla Windows**: `WinKeyLogic` (o mesmo da 1.2) num hook `WH_KEYBOARD_LL`; Win sozinha abre/fecha a Pesquisa e Win + S abre. Teclas injetadas (Visão de tarefas, Win + D, Win + P enviados pela barra) passam direto.
- **Energia**: tudo passa por `AppContext.request_power()`. Com o Shell, o `PowerFx` abre uma cortina por monitor, escurece, mostra a despedida com a contagem (Esc/Cancelar desistem), escurece de vez, executa e (bloquear/suspender/hibernar) some depois. Se desligar/reiniciar não acontecer em 15 s, a cortina sai e avisa.
- **Vidro**: `SetWindowCompositionAttribute` (acrylic) + cantos arredondados e borda do Windows 11 via DWM; sem suporte, as janelas ficam opacas.

## Threads

- **Interface (Qt):** tudo que desenha.
- **Pool `ventude-*` (4 threads):** HTTP, leitura de credenciais, descoberta do Antigravity. O resultado volta por sinal Qt enfileirado (`Tasks.run(..., done=...)`).
- **`ventude-media`:** loop asyncio do WinRT; eventos chegam à interface por sinais.
- **`ventude-audio`:** COM inicializado uma vez; fila de comandos de áudio que junta os repetidos (só o último valor de um slider é aplicado) e relê o estado depois de cada lote.
- **`ventude-keys`:** só com "Tecla Windows abre a Pesquisa do Ventude" ligado: hook `WH_KEYBOARD_LL` com laço de mensagens próprio, para a digitação nunca depender da thread da interface. A decisão de cada tecla fica em `WinKeyLogic` (testável sem hook).
- **`ventude-icons`:** COM em STA; carrega os ícones reais do Windows em fila e entrega `QImage` à interface (o `QPixmap` é feito na thread da interface).
- **Leituras do Sistema, privacidade, lista de janelas da barra, bandeja e arquivos recentes** rodam no pool; o `HardwareMonitor` faz uma leitura por vez.

## Serviços que mexem no sistema

Nenhum serviço que altera o Windows roda antes de `AppContext.start()` / `ShellController.start()` (chamados só pelo app): os testes criam o contexto sem iniciar nada. A barra de tarefas e o hook de teclado têm várias camadas de restauração: desligar o ajuste, `aboutToQuit`, `atexit`, `sys.excepthook` (`ShellController.emergency`) e, para um processo morto à força, o marcador `barra-escondida.json` lido na próxima abertura (que também devolve a área útil original de cada monitor).

## Limites das IAs

Cada provedor é uma função `fetch(FetchContext) -> Report`. O `LimitsManager`:

1. Consulta as IAs ligadas a cada N minutos (padrão 5) e o Antigravity a cada 2 min. IAs desligadas não fazem rede.
2. Em falha passageira, mantém as últimas janelas com `state="stale"` e mostra o motivo.
3. Guarda o último relatório no cache, então a ilha abre já com os números.
4. Calcula o uso "efetivo": quando o horário de renovação passa, a janela aparece zerada até a próxima leitura.
5. Avisa ao cruzar 80% e 95% e quando uma janela que estava acima de 50% renova.

A tabela de Ajustes guarda quais IAs têm anel (`ring_providers`) e qual janela cada anel segue (`pinned_limits_json`, a mesma do alfinete). O anel de cada IA mostra a janela fixada pelo alfinete (Ajustes guardam a chave da janela) ou, sem alfinete, a mais apertada. No Antigravity, o automático considera só os modelos Gemini; cada modelo é uma linha, agrupada por família.

## Interface

- A ilha é uma única janela sem moldura, translúcida e sempre no topo (`Qt.Tool`, fora do Alt+Tab).
- A geometria anima entre pílula (38 px) e painel (860×568) numa curva de mola (`motion.easing("mola")`, passa
  um tiquinho do tamanho e assenta); fechando, recolhe direto. Ao abrir, o painel já fica no tamanho final,
  recortado pela ilha que cresce, e aparece com um fade junto da animação.
- Parada, a superfície da ilha (fundo, brilho "aurora" e fio de luz) fica num pixmap: os filhos que se
  repintam (equalizador, relógio, barras) não refazem gradientes a cada quadro.
- **Abas em grupos** (`config.TAB_GROUPS`): Sistema + Áudio, Estante + Atalhos, Agenda + Tarefas e Mercado +
  Notícias dividem uma aba. A aba ativa cresce com os nomes lado a lado (o tracinho em gradiente marca a página
  aberta) e o indicador desliza até ela. Cada página continua sendo um módulo que liga e desliga sozinho.
- Trocar de página desliza a página nova do lado da aba clicada, com fade.
- **Controles** (Win + A, botão do cabeçalho, botão da ponta direita da Doca e do rodapé do Lançador): Rede,
  Cafeína, Microfone, Foco 25, Luz noturna, Silêncio, Conta-gotas e Texto da tela.
- A ilha inteira aceita arrastar arquivos: abre nos Atalhos e não fecha enquanto o arraste dura.
- As páginas são criadas sob demanda. Páginas desligadas nos Ajustes não são criadas e não fazem rede.
- Fonte: a do sistema (Segoe UI Variable no Windows 11, Segoe UI no 10). Ícones em SVG, desenhados na
  densidade da maior tela (no mínimo 2x); `theme.font()` e `theme.color()` ficam em cache.

## Movimento

`motion.BEZIERS` guarda as curvas da casa (as mesmas do CSS `cubic-bezier`): `padrao`, `enfase` (arranca rápido
e assenta devagar), `saida`, `suave`, `mola` e `mola_leve`. `motion.easing(nome)` devolve uma `QEasingCurve`
nativa (a interpolação roda no C++) e `motion.named(nome)` a mesma curva em Python para o `Tween`. Todas as
durações passam por `motion.ms()`, que respeita a velocidade escolhida nos Ajustes e o "sem animações" do
Windows.
