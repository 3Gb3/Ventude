# Progresso

## Ajustes da 3.0 (Calma): tela cheia, Doca e busca no PC inteiro

- [x] **Tela cheia de verdade**: sem a barra do Windows toda janela maximizada ocupa a tela, então a detecção agora ignora janelas maximizadas (`IsZoomed`) e conta jogos sem bordas pela área cliente cobrindo o monitor, além do `SHQueryUserNotificationState` (Direct3D exclusivo e modo apresentação).
- [x] **Doca em jogos**: encostar o mouse embaixo não traz mais a Doca em tela cheia (só a tecla Windows ou um arraste); depois de recuar ela some de vez, sem a faixa de 4 px por cima do jogo. O Eco dos outros monitores também some.
- [x] **Ilha em jogos**: "Sumir em tela cheia" ligado por padrão (migração 9) e, mesmo desligado, a ilha não se reafirma por cima de um jogo em tela cheia.
- [x] **Ilha abre no Início**: entrar pela zona do clima abria sempre o Clima; passar o mouse agora abre o Início (ou a última aba, se reabrir logo). Clicar no clima/anéis/data continua abrindo a aba deles; "Abrir na aba apontada" volta o comportamento antigo.
- [x] **Ícones da Doca**: cores normais (antes os fixados fechados eram dessaturados por uma rotina pixel a pixel em Python, que travava o hover e podia congelar o ícone genérico no cache), 26 px (nítidos, carregados em 2x), fundo da cápsula em cache e hover com leve elevação e escala.
- [x] **Prévias das janelas**: a janela das prévias passou a ser opaca com cantos do Windows 11; o DWM não desenha miniaturas ao vivo dentro de janelas translúcidas por pixel, então antes só aparecia o ícone. Aparecem em 0,26 s.
- [x] **Busca no PC inteiro**: todas as pastas de todos os discos (sem limite de profundidade, das rasas para as fundas) e imagens, vídeos, músicas, documentos e compactados em qualquer lugar; nas pastas do usuário, todos os arquivos. Índice enxuto (só caminho e nome) com busca por `str.find`: ~4 s para varrer, ~6 MB e 1–4 ms por busca nesta máquina. Resultados separados em Pastas, Imagens, Vídeos, Músicas, Documentos e Arquivos, com "Mostrar na pasta".
- [x] **Filtros**: `img:`/`foto:`, `video:`, `musica:`/`audio:`, `doc:` e `pasta:` (ou `/i`, `/v`, `/m`, `/d`, `/p`).
- [x] **Sites**: digitar "youtube", "gmail", "zap", "netflix"... oferece abrir o site no navegador (60 sites comuns, com apelidos), e endereços digitados ("terra.com.br", "localhost:3000") abrem direto.

- [x] **Busca nas pastas do sistema**: AppData (Roaming, Local...), Program Files, ProgramData, Windows e pastas ocultas (.minecraft) aparecem na busca, alguns níveis dentro e sem arquivos; `%appdata%` e outras variáveis abrem direto.
- [x] **Lançador acima da Doca**: com o Início cheio (duas linhas de fixados, quatro recentes e a dica) o conteúdo passava da altura padrão e o Qt esticava a janela para baixo, por cima da Doca. Agora a altura parte do que o conteúdo pede e a janela é ancorada pela base.
- [x] **Grok pelo Firefox**: o "Copiar como cURL (Windows)" do Firefox começa com `curl.exe` e era lido como se fosse o cookie inteiro (grok.com: "No credentials presented"). Agora é reconhecido.
- [x] **Controles repaginados**: 480 px, seção Som com o nome inteiro da saída e do microfone, teclas com nome e estado, ferramentas lado a lado, "Em segundo plano" com até 8 apps e altura que acompanha o conteúdo. O menu do botão direito não fica mais por baixo da ilha.
- [x] **Clima**: a chance de chuva aparece em todos os dias (antes só a partir de 20%), mais apagada quando é baixa.

### A validar no seu PC

- Jogos em tela cheia exclusiva e sem bordas, vídeo em F11 no navegador e apresentação: Doca, ilha e Eco somem; janela maximizada não.
- Prévias ao vivo ao parar o mouse num app aberto da Doca.

## Versão 2.0.0: Ventude Shell (branch `2.0.0`)

Ainda não está na `main`. O Ventude deixa de ser só uma Dynamic Island e passa a substituir a barra de tarefas, o Menu Iniciar e a Pesquisa do Windows.

- [x] **Barra de tarefas** (uma por monitor, opcional só no principal): clima com aviso de chuva; Iniciar, Pesquisa e Visão de tarefas; fixados + janelas abertas agrupadas pelo AppUserModelID, pacote da Loja ou .exe, com ícones reais do Windows; indicador animado (aberto/em foco), ícone que aparece com um "pop" e pula enquanto o app abre; clique foca/minimiza/alterna, clique do meio abre outra janela, menu com fixar/desafixar (barra e Iniciar), abrir local, maximizar/restaurar e fechar; prévias ao vivo (DWM) ao parar o mouse; ícones ocultos da bandeja; rede/volume/bateria (roda muda o volume); relógio com data; cantinho de mostrar a área de trabalho; menu da barra (Gerenciador de Tarefas, alinhar, ocultar automaticamente, voltar para a barra do Windows).
- [x] **Área útil reservada**: janelas maximizadas param acima da barra; com "ocultar automaticamente", usam a tela toda e a barra volta ao encostar o mouse embaixo. Em tela cheia, a barra some.
- [x] **Menu Iniciar**: pesquisa no topo; Fixado (3 linhas, "Mostrar mais"); Recente (abertos pelo Ventude + arquivos recentes do Windows, "Mostrar tudo"); Todos por Categoria (IA, Desenvolvimento, Navegadores, Comunicação, Produtividade, Entretenimento, Jogos, Criatividade, Utilitários), Grade ou Lista; rodapé com foto e nome da conta, Ajustes, Explorador e Energia. Fixados iniciais: apps conhecidos que estiverem instalados.
- [x] **Pesquisa** (tecla Windows, Win + S, lupa da barra): Pesquisas recentes com hora relativa e X para tirar; resultados por seção com Melhor correspondência e painel de ações (Abrir, Executar como administrador, Abrir local, Fixar no Iniciar/na barra, Terminal/editor, Copiar caminho); páginas de Configurações do Windows e itens do sistema (Lixeira, Painel de Controle...); "Pesquisar na web"; todos os comandos do Ventude.
- [x] **Painel rápido**: rede (Wi-Fi com o nome da rede ou Ethernet), Bluetooth, Cafeína, Microfone, Conta-gotas, Capturar texto, Foco, Luz noturna, Projetar, volume e saída de áudio. **Calendário** com a agenda do dia e Foco de 25 min.
- [x] **Animações de energia**: cortina em todas as telas com "Até logo!" (desligar), "Já volto!" (reiniciar), "Até daqui a pouco!" (suspender), "Até mais tarde!" (hibernar), "Até já!" (bloquear), "Até a próxima, Fulano!" (sair) e as versões com atualização; contagem com Esc/Cancelar; cortina some depois de bloquear/suspender; aviso se o Windows não desligar. "Bom dia/Boa tarde/Boa noite, Fulano!" ao ligar o PC.
- [x] **Abrir e fechar animados**: Iniciar, Pesquisa e painéis sobem com fade; os blocos do Iniciar entram em cascata; a barra sobe ao iniciar e desce em tela cheia.
- [x] **Ilha mais enxuta**: com o Shell, energia, cafeína, conta-gotas e OCR saem do topo do painel; data e clima saem da pílula (ajustes "Data na ilha" e "Clima na ilha"); digitar ou Ctrl + K abre a Pesquisa; o atalho global com "abre a barra de comandos" abre a Pesquisa.
- [x] **Ajustes > Ventude Shell**: barra, tecla Windows, alinhamento, ocultar automaticamente, clima na barra, todos os monitores, vidro, animação de energia e saudação. Bandeja com "Barra de tarefas do Ventude".
- [x] Testes novos (154 no total), prévias `scripts/preview.py --shell` e documentação.

### Conferido nesta máquina

- Rodando de verdade (sem esconder a barra do Windows): 3 barras (uma por monitor) com vidro, janelas abertas agrupadas com AppID, prévia DWM registrada, Iniciar/Pesquisa/painel rápido abrindo com vidro e ícones reais; a barra do Windows não foi tocada.

### A validar no seu PC

- Ligar o Shell de verdade: a barra do Windows some e volta (desligar o ajuste, sair, "Voltar para a barra do Windows"), janelas maximizadas acima da barra nos 3 monitores e com escalas diferentes.
- Tecla Windows abrindo a Pesquisa (e Win + E/D/L/V/Tab, Win + Shift + S continuando do Windows).
- Prévias ao vivo ao parar o mouse num app aberto. Arrastar para reordenar os fixados ainda não existe (no Iniciar, use "Mover para o início").
- Animação de energia de verdade: bloquear e suspender (a cortina some ao voltar); desligar/reiniciar com apps abertos.
- Saudação ao ligar o PC (só aparece nos primeiros 4 minutos depois de ligar).
- Ícones ocultos da bandeja (registro `NotifyIconSettings` do Windows 11) e o botão "Bandeja do Windows".

## Versão 1.2.0 (branch `new_version_1.2`)

Ainda não está na `main`: outros ajustes entram nesta branch antes.

- [x] **Clima na ilha**: ícone e temperatura de agora ao lado da hora (cidade da aba Clima). Ajuste "Clima na ilha" para desligar. Atualiza sozinho a cada 15 min.
- [x] **Novas IAs**: Codex/ChatGPT (janela de 5h e semanal, pelo login do Codex ou cURL do chatgpt.com), Cursor (uso do plano no ciclo, pelo login do Cursor instalado ou cookie do cursor.com) e GitHub Copilot (requisições premium, chat e autocompletar do mês, pelo login da extensão ou token). Começam desligadas.
- [x] **Escolher IAs e limites**: tabela em Ajustes com, para cada IA, "Ligada", "Na ilha" (anel na ilha e no Início) e "Anel mostra" (automático, sessão de 5h, semana, modelo...). As seções de cada IA só aparecem quando ela está ligada.
- [x] Aba IAs em grade (até 3 por linha) e anéis do Início menores quando há mais de 3.

- [x] **Clima central**: um só estado e um só relógio de 15 min para a ilha, o Início e a aba Clima (antes eram três buscas separadas). Cidade estimada pelo IP (ipinfo.io, ipwho.is de reserva) quando nenhuma foi escolhida. Aviso de chuva (≥ 60% na hora atual ou na próxima) na ilha.
- [x] **Pílula interativa**: clique por área (anéis → IAs, clima → Clima, data → Agenda, hora/música → Início, estante → Estante); com o mouse em cima, anéis e clima abrem direto na aba deles. Sobre a música a abertura espera 0,65 s para dar tempo de: clique do meio ou na capa (tocar/pausar), roda (volume do Windows) e Shift + roda ou roda lateral (trocar de faixa).
- [x] Anéis em alerta (amarelo/vermelho) mantêm o trilho e um ponto central na cor da IA.
- [x] Largura da pílula pelo texto de verdade (data, clima, timer, estante, hora, música e anéis), entre 150 e 560 px. Linha fina com o andamento da música.
- [x] **Avisos na ilha**: a ilha cresce para ~400×62 por alguns segundos com limites das IAs (80%/95% e renovação), música nova e lembretes da Agenda (10 e 5 min antes). Com o painel aberto, em tela cheia ou com o ajuste desligado, os importantes vão para a bandeja.
- [x] **Limites**: detecção de Codex/Cursor/Copilot instalados (primeira abertura da 1.2 e botão em Ajustes); projeção "acaba em ~X" pelo ritmo desde o início da janela (ou pela última hora, quando a duração da janela não é conhecida); histórico em SQLite (amostra a cada 4 min, 8 dias) com mini-gráfico em cada IA.
- [x] **Timer e Pomodoro** (25 + 5) no Início e na barra de comandos, com contagem e anel na ilha e pulso + aviso ao terminar.
- [x] **Barra de comandos**: atalho global (ajustável), Ctrl + K ou começar a digitar; atalhos, abas, `t:` tarefa, `a:` compromisso (hoje/amanhã e horário), `timer 15m`, `pomodoro`, `parar`, `cor`, `ocr`.
- [x] **Estante** (aba nova, já ligada): arrastar para a ilha mostra duas áreas, "Guardar na estante" e "Adicionar aos atalhos". Arquivos, pastas, imagens (inclusive do navegador), links e textos; arrastar para fora, colar da área de transferência, copiar, abrir. Some ao fechar o Ventude. Contador na ilha.
- [x] **Conta-gotas** com lupa (HEX na área de transferência) e **captura de texto** por região com o OCR do Windows (Windows.Media.Ocr via WinRT), no topo do painel.

### Pacote "Ventude Shell & Sistema"

Tudo via ctypes/WinRT e biblioteca padrão (sem psutil, pywin32 ou comtypes).

- [x] **Polimentos**: a pílula some antes do aviso e o aviso entra junto com a expansão; Ctrl + 0 e Ctrl + , abrem Ajustes; digitar com a busca aberta (e o foco fora do campo) continua o texto em vez de apagar.
- [x] **Aba Sistema**: CPU (`GetSystemTimes`, núcleos, temperatura pela zona térmica do ACPI quando o PC informa, 3 processos que mais usam), GPU (NVML nas NVIDIA: uso, VRAM, temperatura e consumo; contadores PDH de GPU Engine/Adapter Memory nas demais, com o nome e a VRAM total do registro do driver), RAM (`GlobalMemoryStatusEx` + 3 processos com mais memória), rede (`GetIfTable2`, só placas físicas, sem filtros/VPN, contadores de 64 bits; ping ICMP sem administrador) e disco/bateria. Gráfico dos últimos 60 s. Botões para copiar o IP local e o público (api.ipify.org, só ao clicar).
- [x] **Métricas na pílula**: alfinete em CPU, GPU, temperatura da GPU, RAM, download, upload, ping e bateria (`pinned_metrics_json`). A largura usa o texto mais largo possível de cada métrica, então a ilha não "pula" a cada leitura; clique abre a aba Sistema. Leitura a cada 1,5 s só com a aba aberta ou com métrica fixada; fora isso, uma leitura leve a cada 20 s cuida dos avisos de GPU/CPU acima de 85 °C e RAM acima de 92% (no máximo um a cada 10 min).
- [x] **Aba Áudio**: saídas e entradas ativas (`IMMDeviceEnumerator`), troca do padrão nos três papéis (`IPolicyConfig::SetDefaultEndpoint`), volume e mudo do master e do microfone (`IAudioEndpointVolume`) e mixer por aplicativo agrupado por .exe, com o ícone real (`IAudioSessionManager2` + `ISimpleAudioVolume`). Thread própria (`ventude-audio`) que junta comandos repetidos (arrastar o slider).
- [x] **Volume na pílula**: a roda muda o volume direto pelo Core Audio (2% por "dente", sem o aviso do Windows) e mostra "Volume 68%" na ilha, com ou sem música.
- [x] **Privacidade**: ponto laranja (microfone) e verde (câmera) na pílula pelo `CapabilityAccessManager\ConsentStore` (`LastUsedTimeStop == 0`), com o nome do app; clique no laranja muta/desmuta o microfone com aviso.
- [x] **Modo Cafeína**: xícara no topo do painel, `cafeina`, `cafeina 30m`, `cafeina 2h`, `cafeina off` na busca; `SetThreadExecutionState`; ícone na pílula enquanto ligado; desliga ao sair.
- [x] **Histórico da área de transferência na Estante**: últimos 20 textos, links, cores HEX, imagens e arquivos copiados; respeita os formatos que gerenciadores de senha usam para ficar fora de históricos; filtro Tudo/Copiados/Fixados; itens fixados ficam no SQLite (imagens numa pasta própria) e voltam ao abrir. O histórico não conta no contador da pílula.
- [x] **QR Code** nativo (gerador próprio, ISO 18004: modo byte, Reed–Solomon, máscaras, versões 1–40) num cartão dentro da ilha: menu da Estante ("Mostrar QR Code") e `qr <texto>` na busca. Conferido contra os vetores da norma.
- [x] **Letra sincronizada** (lrclib.net, cache no SQLite: 30 dias com letra, 1 dia sem) no player do Início, verso a verso.
- [x] **Busca universal / Menu Iniciar**: apps do menu Iniciar (.lnk/.appref-ms) e da Loja (Get-StartApps), pastas e arquivos (Área de Trabalho, Downloads, Documentos, Imagens, Vídeos, Músicas, pasta do usuário, OneDrive e raízes dos discos, até 3–4 níveis, sem node_modules/.git/.venv/AppData etc., no máximo 60 mil itens, refeito a cada 15 min). `/p` ou `pasta:` só pastas; Enter abre, Ctrl + Enter Terminal, Shift + Enter Cursor/VS Code (também por botões). Calculadora segura (árvore da expressão, sem eval), com vírgula decimal e `^`.
- [x] **Tecla Windows** (ajuste, desligado por padrão): hook `WH_KEYBOARD_LL` numa thread própria. Win sozinha e Win + S abrem a busca; outros atalhos com Win seguem para o Windows. Para o Iniciar nativo não abrir, a soltura do Win é bloqueada e reinjetada depois de uma tecla-máscara, numa única chamada `SendInput` (o Win nunca fica "preso"). Removido ao desligar e ao sair.
- [x] **Central de Energia**: botão no topo do painel (com selo quando há atualização do Windows esperando reiniciar) e comandos na busca: bloquear, suspender, hibernar, reiniciar, desligar, sair da sessão e, com atualização pendente, atualizar e reiniciar/desligar (`UsoClient StartInstall` + `InitiateShutdownW` com `SHUTDOWN_INSTALL_UPDATES`). Destrutivas pedem um segundo clique ou Enter.
- [x] **Dock do Ventude** (ajuste, desligado por padrão): esconde `Shell_TrayWnd`/`Shell_SecondaryTrayWnd` e libera a área útil; dock em pílula com o botão do Ventude, os Atalhos fixados e as janelas abertas (regras do Alt + Tab, `DWMWA_CLOAKED`, apps da Loja pelo processo real), ponto/traço de aberto/em foco, clique foca ou minimiza, clique do meio fecha, menu com focar, minimizar, maximizar/restaurar, fechar e fixar nos Atalhos; some com janela maximizada/tela cheia e volta ao encostar o mouse embaixo. A barra do Windows volta ao desligar o ajuste, ao sair (`aboutToQuit` e `atexit`), em erro não tratado (`sys.excepthook`) e, se o Ventude for morto, na próxima abertura (arquivo marcador com as áreas originais).

### A validar no seu PC

- **Dock e tecla Windows** no seu Windows 11 (ficam desligados até você ligar em Ajustes > Geral). Conferir: barra some e volta ao desligar; Win sozinha não abre o Iniciar nativo; Win + E/D/L/V/Tab/setas e Win + Shift + S continuam normais; ícones do Dock (apps da Loja).
- Troca de saída/entrada padrão e mixer com os seus fones/HDMI (a leitura de dispositivos e sessões já foi conferida nesta máquina).
- Ponto de privacidade com Discord/Teams usando o microfone e a câmera.
- GPU NVIDIA (NVML) e a temperatura do processador (muitas placas não informam pela zona térmica do ACPI).
- Central de Energia de verdade (os testes nunca executam as ações) e "atualizar e reiniciar" com uma atualização pendente.
- Escanear o QR Code com o celular.

- Codex, Cursor e Copilot com as suas contas: os endpoints de uso deles não são APIs públicas documentadas.
- OCR do Windows (precisa de um idioma de OCR instalado: Configurações > Hora e idioma > Idioma) e conta-gotas com mais de um monitor e escalas diferentes.
- Arrastar da Estante para o navegador, o e-mail e o Discord; imagens arrastadas do navegador.
- Volume pela roda do mouse e lembretes da Agenda.

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
