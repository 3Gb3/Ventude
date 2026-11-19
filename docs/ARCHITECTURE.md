# Arquitetura

Python 3.12 + PySide6-Essentials (só QtCore, QtGui, QtWidgets, QtSvg e QtNetwork). HTTP, SQLite, DPAPI e registro do Windows vêm da biblioteca padrão. WinRT só é instalado no Windows, para a mídia.

```
src/ventude/
  app.py               inicialização, instância única, bandeja, ilha
  core/
    config.py          Settings (dataclass) e lista de abas
    store.py           SQLite único: ajustes, atalhos, agenda, tarefas, cache, segredos + migração
    net.py             HTTP sobre urllib (JSON, erros com status, TLS local para o Antigravity)
    tasks.py           pool de threads que entrega o resultado na thread da interface
    secrets.py         DPAPI (CryptProtectData) via ctypes
    timefmt.py         datas e durações em português
  limits/
    model.py           Window, Report, FetchContext, ProviderError
    claude.py          Claude Code (OAuth, somente leitura) e claude.ai (sessionKey)
    antigravity.py     descoberta do language server local + RPC de cotas
    grok.py            Grok CLI (créditos) e grok.com (consultas por modo)
    manager.py         agenda consultas, cache, estado "stale", avisos de 80/95% e renovação
  services/
    media.py           GlobalSystemMediaTransportControls (WinRT, sem polling)
    weather.py         Open-Meteo
    market.py          Yahoo Finance (chart v8)
    news.py            RSS/Atom
    launcher.py        abrir apps, pastas, arquivos e sites
    system.py          iniciar com o Windows, atalho global, detecção de tela cheia
  ui/
    theme.py           paleta, fonte do sistema e folha de estilo única
    icons.py           ícones SVG de traço (sem fontes de ícones)
    widgets.py         Card, Ring, UsageBar, Sparkline, Switch, CheckCircle, Equalizer...
    context.py         AppContext: serviços + ajustes vivos + sinais compartilhados
    island.py          janela da ilha: pílula, painel, animação, arraste, hover, atalho
    tray.py            ícone e menu da bandeja, avisos
    pages/             uma classe por aba, criada só quando a aba é aberta
```

## Threads

- **Interface (Qt):** tudo que desenha.
- **Pool `ventude-*` (4 threads):** HTTP, leitura de credenciais, descoberta do Antigravity. O resultado volta por sinal Qt enfileirado (`Tasks.run(..., done=...)`).
- **`ventude-media`:** loop asyncio do WinRT; eventos chegam à interface por sinais.

## Limites das IAs

Cada provedor é uma função `fetch(FetchContext) -> Report`. O `LimitsManager`:

1. Consulta Claude e Grok a cada N minutos (padrão 5) e o Antigravity a cada 2 min.
2. Em falha passageira, mantém as últimas janelas com `state="stale"` e mostra o motivo.
3. Guarda o último relatório no cache, então a ilha abre já com os números.
4. Calcula o uso "efetivo": quando o horário de renovação passa, a janela aparece zerada até a próxima leitura.
5. Avisa ao cruzar 80% e 95% e quando uma janela que estava acima de 50% renova.

O anel de cada IA mostra a janela mais apertada. No Antigravity, prioriza os modelos Gemini.

## Interface

- A ilha é uma única janela sem moldura, translúcida e sempre no topo (`Qt.Tool`, fora do Alt+Tab).
- A geometria anima entre pílula (38 px) e painel (860×568). O conteúdo aparece com um fade curto.
- As abas são criadas sob demanda. Abas desligadas nos Ajustes não são criadas e não fazem rede.
- Fonte: a do sistema (Segoe UI Variable no Windows 11, Segoe UI no 10). Ícones em SVG.
