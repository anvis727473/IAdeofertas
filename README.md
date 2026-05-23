# 🤖 AliExpress → Telegram Offer Bot

Bot assíncrono de automação de ofertas: busca produtos do AliExpress, gera links de afiliado e dispara cards formatados para um canal do Telegram. Projetado para deploy como **Background Worker** no [Render](https://render.com).

---

## Estrutura de arquivos

```
.
├── main.py           # Motor assíncrono principal (worker loop)
├── database.py       # Camada de dados Supabase
├── requirements.txt  # Dependências Python pinadas
└── schema.sql        # Script DDL para o Supabase
```

---

## 1. Supabase — Preparação do banco

1. Acesse o [SQL Editor do Supabase](https://app.supabase.com) no seu projeto.
2. Cole o conteúdo de `schema.sql` e execute.
3. Anote **Project URL** e **service_role API key** (Settings → API).

> ⚠️ Use sempre a `service_role` key no bot — ela bypassa o RLS e é necessária para escrita. **Nunca a exponha no front-end.**

---

## 2. Telegram — Configuração do bot e canal

1. Crie o bot via [@BotFather](https://t.me/BotFather) e copie o token.
2. Adicione o bot como **administrador** do canal de destino.
3. Obtenha o `CHAT_ID` do canal:
   - Canais públicos: `@nome_do_canal`
   - Canais privados: use `https://api.telegram.org/bot<TOKEN>/getUpdates` após enviar uma mensagem.

---

## 3. AliExpress — Credenciais de afiliado

1. Acesse o [AliExpress Portals](https://portals.aliexpress.com/) e crie um app.
2. Copie o **App Key** (= `ALI_API_KEY`).
3. Configure um **Tracking ID** (= `ALI_TRACKING_ID`) na aba de rastreamento.

---

## 4. Deploy no Render

### 4.1 Criar o serviço

| Campo            | Valor                          |
|------------------|-------------------------------|
| **Type**         | **Background Worker**          |
| **Runtime**      | Python 3                       |
| **Build Command**| `pip install -r requirements.txt` |
| **Start Command**| `python main.py`               |
| **Instance Type**| Free (512 MB RAM) ou Starter   |

### 4.2 Variáveis de ambiente (Environment → Add Variable)

| Variável          | Descrição                                      | Exemplo                        |
|-------------------|------------------------------------------------|--------------------------------|
| `SUPABASE_URL`    | URL do projeto Supabase                        | `https://xxxx.supabase.co`     |
| `SUPABASE_KEY`    | service_role key do Supabase                   | `eyJhbGci...`                  |
| `TELEGRAM_TOKEN`  | Token do bot do Telegram                       | `123456:ABC-DEF...`            |
| `CHAT_ID`         | ID ou @username do canal                       | `@minhas_ofertas` ou `-1001...`|
| `ALI_API_KEY`     | App Key do AliExpress Portals                  | `12345678`                     |
| `ALI_TRACKING_ID` | Tracking ID padrão do afiliado                 | `default`                      |
| `POLL_INTERVAL`   | Segundos entre ciclos (padrão: `300`)          | `300`                          |
| `BATCH_SIZE`      | Ofertas por ciclo (padrão: `5`)                | `5`                            |
| `SEND_DELAY`      | Pausa entre envios em segundos (padrão: `3.0`) | `3.0`                          |

### 4.3 Auto-Deploy

Conecte o repositório GitHub ao Render. A cada push na branch `main`, o Render fará rebuild e restart automático do worker.

---

## 5. Adicionando ofertas

Insira registros diretamente na tabela `ofertas` do Supabase:

```sql
INSERT INTO public.ofertas (
    titulo, url_produto, url_imagem,
    preco_original, preco_desconto, percentual_desconto,
    cupom, prioridade, agendado_para
) VALUES (
    'Produto Exemplo',
    'https://www.aliexpress.com/item/1005001234567890.html',
    'https://ae01.alicdn.com/kf/imagem.jpg',
    199.90, 99.90, 50.0,
    'CUPOM10', 10,
    NOW() + INTERVAL '2 hours'   -- agendado para daqui a 2h; NULL = imediato
);
```

O bot detecta o novo registro no próximo ciclo de polling e envia automaticamente.

---

## 6. Arquitetura e decisões técnicas

```
Render Background Worker
│
├── asyncio event loop (single-threaded, non-blocking)
│   ├── httpx.AsyncClient     — pool de conexões reutilizado
│   ├── tenacity retries      — exponential backoff para AliExpress + Telegram
│   └── asyncio.sleep()       — zero CPU durante o intervalo de polling
│
└── Supabase (PostgreSQL)
    ├── UNIQUE INDEX parcial   — evita duplicidade de URL pendente
    ├── RPC increment_tentativas — incremento atômico sem race condition
    └── RLS habilitado         — acesso restrito à service_role key
```

**Consumo de memória estimado no Render Free tier:** ~60–90 MB (sem dependências pesadas como pandas ou numpy).

---

## 7. Monitoramento

Acesse **Render → seu serviço → Logs** para acompanhar os ciclos em tempo real:

```
2025-01-15T10:00:00 [INFO] bot.main: Bot iniciado. Intervalo de polling: 300s | Batch: 5
2025-01-15T10:00:01 [INFO] bot.main: 2 oferta(s) encontrada(s) para envio.
2025-01-15T10:00:03 [INFO] bot.main: Oferta abc-123 enviada ao Telegram com sucesso.
2025-01-15T10:00:06 [INFO] bot.main: Ciclo concluído.
2025-01-15T10:05:06 [DEBUG] bot.main: Dormindo por 300s...
```
