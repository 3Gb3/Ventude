# Progresso

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
