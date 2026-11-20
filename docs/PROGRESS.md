# Progresso

## Versão 1.2.0 (em andamento, branch `new_version_1.2`)

Ainda não está na `main`: outros ajustes entram nesta branch antes.

- [x] **Clima na ilha**: ícone e temperatura de agora ao lado da hora (cidade da aba Clima). Ajuste "Clima na ilha" para desligar. Atualiza sozinho a cada 15 min.
- [x] **Novas IAs**: Codex/ChatGPT (janela de 5h e semanal, pelo login do Codex ou cURL do chatgpt.com), Cursor (uso do plano no ciclo, pelo login do Cursor instalado ou cookie do cursor.com) e GitHub Copilot (requisições premium, chat e autocompletar do mês, pelo login da extensão ou token). Começam desligadas.
- [x] **Escolher IAs e limites**: tabela em Ajustes com, para cada IA, "Ligada", "Na ilha" (anel na ilha e no Início) e "Anel mostra" (automático, sessão de 5h, semana, modelo...). As seções de cada IA só aparecem quando ela está ligada.
- [x] Aba IAs em grade (até 3 por linha) e anéis do Início menores quando há mais de 3.

### A validar no seu PC

- Codex, Cursor e Copilot com as suas contas: os endpoints de uso deles não são APIs públicas documentadas.

## Versão 1.1.0

Ajustes pedidos depois da 1.0 (branch `new_version_1.1`). Ao entrar na `main`, o build publica a **1.1.0**.

- [x] **Claude e Grok conectando**: o claude.ai e o grok.com respondiam com o desafio do Cloudflare ("Just a moment...") para o Python puro. Agora a conexão usa o `curl_cffi` (igual à do Chrome). Conferido: o mesmo pedido que antes recebia o desafio passa a receber a resposta da API.
- [x] Claude Code: o token vencido (dura poucas horas) é renovado como o próprio Claude Code faz e gravado no mesmo arquivo, sem mexer no resto. Ajuste para desligar. Também procura o login no WSL quando a distro está aberta.
- [x] claude.ai e grok.com aceitam o "Copiar como cURL" (bash, cmd ou PowerShell): vão juntos cookie, User-Agent e cabeçalhos extras. Mensagens claras para cookie vencido, cookie sem login e bloqueio do Cloudflare. Os Ajustes mostram se conectou.
- [x] **Gemini**: cada modelo vira uma linha, agrupada por família (Gemini, Claude, GPT, Outros). Modelos diferentes não são mais somados numa linha só. Codinomes internos (ex.: argon) vão para "Outros" e nunca para o anel.
- [x] O anel da ilha segue os modelos Gemini e o **alfinete** na aba IAs fixa qualquer limite no anel (de qualquer IA).
- [x] **Arrastar para os Atalhos**: a ilha inteira aceita arquivos, pastas, apps e links, mesmo fechada; abre nos Atalhos, não fecha durante o arraste e funciona com a lista vazia.
- [x] **Só o Spotify**: a mídia segue e controla só o Spotify (Ajustes > Música volta para qualquer player).
- [x] Música **pausada continua na ilha**, com título e capa apagados.
- [x] **Animação ao trocar de aba** (desliza do lado da aba clicada, com fade).
- [x] **Abre na hora** ao passar o mouse (antes esperava 0,16 s; agora ajustável) e o conteúdo aparece junto com a animação de abertura.
- [x] **Clima por dia**: clique num dia (na aba Clima ou no card do Início) para ver umidade, chuva, vento, UV, sensação, nascer e pôr do sol e o gráfico hora a hora com detalhes ao passar o mouse.
- [x] Versão: o primeiro build de cada linha publica a versão do `pyproject.toml` (1.1.0), os seguintes sobem o último número.

### A validar no seu PC

- Claude via claude.ai e Grok via grok.com com a sua conta (o Cloudflare também olha o IP; se barrar, use o "Copiar como cURL").
- Renovação do token do Claude Code com o Claude Code instalado.
- Arrastar do Explorer, do menu Iniciar e do navegador para a ilha.
- Spotify pausado e retomado; Spotify da loja e do site.
- Primeiro build da `main` gerando a Release v1.1.0 (o log de teste deve mostrar "Conexão com claude.ai/grok.com: curl_cffi").

## Versão 1.0: completa

Reformulação completa do Ventude (19/11/2026).

- [x] IA antiga (ChatGPT/Codex, login, ferramentas, histórico) removida, com as dependências dela (`jsonschema`, `pyjwt`, `cryptography`, `httpx`).
- [x] **Limites das IAs**: Claude (Claude Code ou claude.ai), Gemini no Antigravity (servidor local) e Grok (Grok CLI ou grok.com), com anéis na ilha, aba dedicada, cache, estado "último valor" e avisos de 80%, 95% e renovação.
- [x] Ilha única no lugar das duas: pílula com data, hora, música (capa e equalizador) e anéis; painel com 9 abas criadas sob demanda.
- [x] Visual novo: paleta escura, fonte do sistema, ícones SVG, componentes desenhados à mão (anéis, barras, sparklines, switches).
- [x] Início com relógio, semana, próximo compromisso, clima com 3 dias, IAs, player e tarefas.
- [x] Atalhos em blocos com ícone real do Windows, arrastar e soltar, sites, renomear e reordenar.
- [x] Agenda com horário flexível (`14h`, `9-10`) e próximos dias. Tarefas com várias listas e edição inline.
- [x] Clima com 7 dias e faixa de temperatura. Mercado com lista editável e gráfico do mês. Notícias com filtro por fonte.
- [x] Ajustes ao vivo: iniciar com o Windows, atalho global Ctrl+Alt+Espaço, abrir ao passar o mouse, sumir em tela cheia, abas, fontes de notícias e fontes de cada IA.
- [x] Leveza: só PySide6-Essentials + biblioteca padrão (WinRT só no Windows). Um SQLite único. Abas desligadas não rodam.
- [x] Migração automática de atalhos, agenda e listas da versão anterior. Arquivos antigos vão para `legado\`.
- [x] Instância única, ícone próprio, spec do PyInstaller enxuto.

## Instalador e atualização automática

- [x] Instalador `Ventude-Setup.exe` (Inno Setup): sem administrador, atalho na área de trabalho e no menu Iniciar, desinstalador.
- [x] GitHub Actions: a cada push na `main` testa, gera o executável num Windows real, testa se ele abre e publica a Release `vX.Y.N`.
- [x] Ao iniciar, o app instalado confere a última Release da `main` e pergunta se você quer atualizar. Ele baixa, confere o SHA-256, instala em silêncio e reabre.

## Verificação

- `uv run ruff check`: sem apontamentos.
- `uv run pytest`: 57 testes passando (núcleo, migração, rede local, provedores de IA, gerenciador de limites, serviços, atualização e interface).
- App iniciado de verdade (Qt offscreen): sobe, fica rodando e uma segunda execução só pede para a primeira aparecer.
- PyInstaller com o spec novo: build concluído e o executável abre.
- Todas as telas conferidas visualmente com `scripts/preview.py`.

## Validar no seu PC

Os pontos abaixo dependem do Windows ou das suas contas e não puderam ser testados no ambiente de desenvolvimento:

- Leitura real das cotas no Antigravity aberto, no Claude Code logado e no Grok.
- Player do Windows, atalho global, tela cheia e "Iniciar com o Windows".
- Primeira instalação pelo `Ventude-Setup.exe` e a primeira atualização de verdade.
