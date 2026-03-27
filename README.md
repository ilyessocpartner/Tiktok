# Polymarket Trading Bot

> Bot de trading automatisé pour [Polymarket](https://polymarket.com) — marché de prédiction décentralisé sur Polygon.
> Deux stratégies incluses : **Weather Trader** (météo) et **Fast Loop** (BTC court terme).

---

## Avertissement important

> **⚠️ AVERTISSEMENT — LISEZ AVANT TOUTE UTILISATION**
>
> Ce logiciel est fourni **à titre éducatif uniquement**. Le trading sur les marchés de prédiction implique un **risque de perte totale du capital investi**. Les performances passées ne garantissent pas les performances futures. Ce bot n'est **pas un conseil financier**. Utilisez-le uniquement avec des fonds que vous pouvez vous permettre de perdre. L'auteur décline toute responsabilité en cas de perte financière liée à l'utilisation de ce logiciel.
>
> **Commencez TOUJOURS avec `DRY_RUN=true` avant d'activer le trading réel.**

---

## Table des matières

1. [Vue d'ensemble](#vue-densemble)
2. [Prérequis](#prérequis)
3. [Démarrage rapide](#démarrage-rapide)
4. [Configuration](#configuration)
5. [Stratégies](#stratégies)
6. [Gestion des risques](#gestion-des-risques)
7. [Déploiement VPS](#déploiement-vps)
8. [Commandes Telegram](#commandes-telegram)
9. [Architecture](#architecture)

---

## Vue d'ensemble

Ce bot surveille en continu les marchés Polymarket et exécute des ordres selon deux stratégies distinctes :

- **Weather Trader** : exploite les écarts entre les prévisions météo (NOAA) et les cotes du marché pour des questions du type « La température à NYC dépassera-t-elle 90°F demain ? »
- **Fast Loop** : suit les mouvements rapides du prix du Bitcoin (flux Binance WebSocket) sur des fenêtres de 5 et 15 minutes pour trader des marchés BTC liés à Polymarket.

Le bot est contrôlable en temps réel via un bot Telegram et dispose d'un gestionnaire de risques qui coupe automatiquement le trading en cas de pertes excessives.

---

## Prérequis

| Composant | Version minimale | Notes |
|-----------|-----------------|-------|
| Python | 3.10+ | Python 3.11 recommandé |
| Portefeuille Polygon | — | Metamask ou équivalent |
| USDC.e sur Polygon | — | Fonds nécessaires pour trader |
| Clés API Polymarket | — | Voir [polymarket.com](https://polymarket.com) → Compte → API Keys |
| Bot Telegram | — | Créer via [@BotFather](https://t.me/BotFather) |
| Chat ID Telegram | — | Obtenir via [@userinfobot](https://t.me/userinfobot) |

### Obtenir les clés API Polymarket

1. Connectez-vous sur [polymarket.com](https://polymarket.com) avec votre wallet.
2. Allez dans **Compte → API Keys**.
3. Générez une nouvelle clé — notez la clé, le secret et la passphrase (ils ne sont affichés qu'une seule fois).

---

## Démarrage rapide

```bash
# 1. Cloner le dépôt
git clone https://github.com/votre-user/polymarket-bot.git
cd polymarket-bot

# 2. Installer les dépendances
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Configurer l'environnement
cp .env.example .env
nano .env   # remplissez vos clés

# 4. Lancer en mode simulation (RECOMMANDÉ pour commencer)
DRY_RUN=true python main.py

# 5. Vérifier les logs Telegram et surveiller le comportement
# 6. Seulement quand vous êtes satisfait, passer à DRY_RUN=false
```

---

## Configuration

Toutes les options sont définies dans le fichier `.env`. Copiez `.env.example` vers `.env` et remplissez vos valeurs.

### Portefeuille et API

| Variable | Description | Exemple |
|----------|-------------|---------|
| `PRIVATE_KEY` | Clé privée du wallet Polygon (préfixe `0x`) | `0xabc123...` |
| `POLY_API_KEY` | Clé API Polymarket | `abc123` |
| `POLY_API_SECRET` | Secret API Polymarket | `xyz789` |
| `POLY_API_PASSPHRASE` | Passphrase API Polymarket | `mypass` |

### Telegram

| Variable | Description | Exemple |
|----------|-------------|---------|
| `TELEGRAM_TOKEN` | Token du bot (fourni par @BotFather) | `123456:ABC...` |
| `TELEGRAM_CHAT_ID` | Votre ID Telegram personnel | `987654321` |

### Sécurité

| Variable | Description | Défaut |
|----------|-------------|--------|
| `DRY_RUN` | Mode simulation — aucun ordre réel envoyé | `true` |

### Activation des stratégies

| Variable | Description | Défaut |
|----------|-------------|--------|
| `ENABLE_WEATHER_TRADER` | Activer la stratégie météo | `true` |
| `ENABLE_FAST_LOOP` | Activer la stratégie BTC court terme | `false` |

### Weather Trader

| Variable | Description | Défaut |
|----------|-------------|--------|
| `WEATHER_ENTRY_THRESHOLD` | Écart minimum (en probabilité) pour entrer | `0.08` |
| `WEATHER_EXIT_THRESHOLD` | Écart minimum pour sortir | `0.08` |
| `WEATHER_MAX_POSITION` | Taille max par position (USDC) | `2.0` |
| `WEATHER_LOCATIONS` | Villes surveillées (séparées par virgules) | `NYC,Chicago,...` |
| `WEATHER_SCAN_FREQ` | Fréquence de scan en secondes | `120` |

### Fast Loop (BTC)

| Variable | Description | Défaut |
|----------|-------------|--------|
| `FAST_LOOP_POSITION_SIZE` | Taille par position (USDC) | `5.0` |
| `FAST_LOOP_MAX_POSITIONS` | Nombre max de positions simultanées | `3` |
| `FAST_LOOP_SCAN_FREQ` | Fréquence de scan en secondes | `5` |
| `FAST_LOOP_EXIT_BEFORE_CLOSE` | Minutes avant clôture pour sortir | `15` |
| `FAST_LOOP_ENTRY_DEVIATION` | Déviation min du prix pour entrer | `0.005` |

### Gestion des risques

| Variable | Description | Défaut |
|----------|-------------|--------|
| `MAX_DAILY_LOSS` | Perte journalière maximale (USDC) | `50.0` |
| `MAX_CONSECUTIVE_LOSSES` | Nombre max de pertes consécutives | `3` |
| `STOP_LOSS_PER_TRADE` | Stop-loss par trade (USDC) | `3.0` |

### Logging

| Variable | Description | Défaut |
|----------|-------------|--------|
| `LOG_LEVEL` | Niveau de log (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

---

## Stratégies

### Weather Trader

Cette stratégie exploite les marchés de type *"La température maximale à [Ville] dépassera-t-elle [X]°F le [Date] ?"*

**Logique de fonctionnement :**

1. Toutes les `WEATHER_SCAN_FREQ` secondes, le bot interroge l'API NOAA pour les prévisions horaires de chaque ville configurée.
2. Il calcule la température maximale journalière prévue (en °F).
3. Il compare cette prévision avec la cote implicite du marché Polymarket correspondant.
4. Si l'écart entre la probabilité implicite du marché et la probabilité calculée depuis la météo dépasse `WEATHER_ENTRY_THRESHOLD`, le bot entre en position.
5. La position est fermée dès que l'écart repasse sous `WEATHER_EXIT_THRESHOLD` ou que le marché est sur le point de se résoudre.

**Source des données météo :**
- API NOAA National Weather Service (gratuite, pas de clé requise)
- Cache des points de grille pour éviter les appels redondants
- Backoff exponentiel en cas de limitation de débit (max 3 tentatives)

### Fast Loop (BTC)

Cette stratégie trade des marchés binaires liés au prix du Bitcoin sur des horizons très courts (5 à 15 minutes).

**Logique de fonctionnement :**

1. Le bot maintient une connexion WebSocket permanente à Binance (`btcusdt@trade`) pour le prix BTC en temps réel.
2. Toutes les `FAST_LOOP_SCAN_FREQ` secondes, il calcule la variation de prix sur les 5 et 15 dernières minutes.
3. Si la variation dépasse `FAST_LOOP_ENTRY_DEVIATION`, il cherche un marché Polymarket adapté (ex : *"BTC sera-t-il au-dessus de X$ dans 15 min ?"*).
4. Un maximum de `FAST_LOOP_MAX_POSITIONS` positions sont maintenues simultanément.
5. `FAST_LOOP_EXIT_BEFORE_CLOSE` minutes avant la résolution du marché, toutes les positions sont liquidées.

**Reconnexion automatique :** si le WebSocket Binance se déconnecte, le bot se reconnecte automatiquement après 5 secondes.

---

## Gestion des risques

Le gestionnaire de risques surveille en permanence l'activité de trading et peut suspendre toutes les stratégies automatiquement.

### Mécanismes de protection

| Mécanisme | Comportement |
|-----------|-------------|
| **Perte journalière maximale** | Si les pertes cumulées du jour dépassent `MAX_DAILY_LOSS` USDC, le trading est suspendu jusqu'au lendemain |
| **Pertes consécutives** | Si `MAX_CONSECUTIVE_LOSSES` trades consécutifs sont perdants, le trading est pausé |
| **Stop-loss par trade** | Chaque position est fermée automatiquement si elle perd plus de `STOP_LOSS_PER_TRADE` USDC |
| **Mode DRY_RUN** | Aucun ordre réel n'est envoyé — le bot simule les trades et loggue les actions |

### Bonnes pratiques recommandées

- Commencez avec `DRY_RUN=true` pendant au moins 48h.
- Utilisez `MAX_DAILY_LOSS` conservateur (ex : 10-20 USDC pour commencer).
- Définissez `WEATHER_MAX_POSITION` et `FAST_LOOP_POSITION_SIZE` à des montants faibles au départ.
- Surveillez les logs et les notifications Telegram régulièrement.

---

## Déploiement VPS

### Installation automatique (Ubuntu 22.04)

```bash
# Cloner le projet sur le VPS
git clone https://github.com/votre-user/polymarket-bot.git /tmp/polymarket-bot
cd /tmp/polymarket-bot

# Lancer le script d'installation
chmod +x scripts/setup.sh
sudo bash scripts/setup.sh
```

Le script effectue les actions suivantes :
- Met à jour `apt` et installe Python 3.11, `python3.11-venv`, `python3-pip`
- Copie les fichiers du projet dans `/opt/polymarket-bot`
- Crée un environnement virtuel et installe les dépendances
- Copie `.env.example` vers `.env` (si absent)
- Crée et active un service systemd `polymarket-bot`

### Gestion du service systemd

```bash
# Démarrer le bot
sudo systemctl start polymarket-bot

# Arrêter le bot
sudo systemctl stop polymarket-bot

# Redémarrer après modification de .env
sudo systemctl restart polymarket-bot

# Voir les logs en temps réel
sudo journalctl -u polymarket-bot -f

# Statut du service
sudo systemctl status polymarket-bot

# Désactiver le démarrage automatique
sudo systemctl disable polymarket-bot
```

### Configuration de l'environnement sur VPS

```bash
# Éditer les variables d'environnement
nano /opt/polymarket-bot/.env

# Vérifier les permissions (la clé privée doit être protégée)
chmod 600 /opt/polymarket-bot/.env
```

---

## Commandes Telegram

Toutes les commandes ne répondent qu'au `TELEGRAM_CHAT_ID` configuré. Tout autre utilisateur reçoit un message d'erreur.

| Commande | Description |
|----------|-------------|
| `/start` | Message de bienvenue avec la liste des commandes |
| `/status` | Affiche l'état de chaque stratégie (active/pausée) et les statistiques du risk manager |
| `/balance` | Affiche le solde USDC du portefeuille Polymarket |
| `/stats` | Affiche les statistiques P&L : profit net, pertes du jour, trades gagnants/perdants |
| `/stop_trading` | Met en pause toutes les stratégies immédiatement |
| `/resume` | Relance toutes les stratégies mises en pause |
| `/help` | Affiche la liste de toutes les commandes disponibles |

Les commandes `/status` et `/stop_trading` sont également accessibles via des **boutons inline** dans les messages du bot.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Polymarket Trading Bot                   │
└─────────────────────────────────────────────────────────────┘

  ┌──────────────┐    ┌──────────────┐    ┌──────────────────┐
  │ Weather      │    │  Fast Loop   │    │  Telegram Bot    │
  │ Trader       │    │  Strategy    │    │  Interface       │
  │ Strategy     │    │              │    │                  │
  └──────┬───────┘    └──────┬───────┘    └────────┬─────────┘
         │                   │                     │
         │    ┌──────────────▼──────────────┐      │
         │    │        Risk Manager         │      │
         │    │  - Daily loss limit         │      │
         │    │  - Consecutive loss stop    │      │
         │    │  - Per-trade stop-loss      │      │
         │    └──────────────┬──────────────┘      │
         │                   │                     │
         │    ┌──────────────▼──────────────┐      │
         │    │      Polymarket Client      │◄─────┘
         │    │  (py-clob-client)           │
         │    │  - Place/cancel orders      │
         │    │  - Get balance              │
         │    │  - Monitor positions        │
         │    └─────────────────────────────┘
         │
  ┌──────▼───────────────────────────────────┐
  │              Data Layer                  │
  │                                          │
  │  ┌──────────────┐   ┌──────────────────┐ │
  │  │  NOAA Client │   │   BTC Feed       │ │
  │  │              │   │  (Binance WS)    │ │
  │  │  - Hourly    │   │                  │ │
  │  │    forecast  │   │  - Real-time     │ │
  │  │  - Daily     │   │    price         │ │
  │  │    high temp │   │  - Price history │ │
  │  │  - Grid cache│   │  - % change      │ │
  │  └──────┬───────┘   └────────┬─────────┘ │
  └─────────┼────────────────────┼───────────┘
            │                    │
            ▼                    ▼
  api.weather.gov       stream.binance.com
  (NOAA NWS API)        (WebSocket WSS)


  Fichiers de configuration :
  ┌─────────────────────────────────────────┐
  │  .env                                   │
  │  ├── Clés wallet Polygon                │
  │  ├── Clés API Polymarket                │
  │  ├── Token Telegram                     │
  │  ├── Paramètres de stratégies           │
  │  └── Limites de risque                  │
  └─────────────────────────────────────────┘
```

### Structure des fichiers

```
polymarket-bot/
├── main.py                     # Point d'entrée principal
├── requirements.txt            # Dépendances Python
├── .env.example                # Template de configuration
├── .env                        # Configuration locale (ne pas commiter)
│
├── bot/
│   ├── __init__.py
│   ├── telegram_bot.py         # Interface Telegram
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── noaa_client.py      # Client API météo NOAA
│   │   └── btc_feed.py         # Flux prix BTC (Binance WebSocket)
│   │
│   ├── strategies/
│   │   ├── __init__.py
│   │   ├── weather_trader.py   # Stratégie météo
│   │   └── fast_loop.py        # Stratégie BTC court terme
│   │
│   ├── risk/
│   │   ├── __init__.py
│   │   └── risk_manager.py     # Gestionnaire de risques
│   │
│   └── utils/
│       ├── __init__.py
│       └── logger.py           # Configuration des logs colorés
│
├── config/
│   └── settings.py             # Chargement de la configuration
│
└── scripts/
    └── setup.sh                # Script d'installation VPS
```

---

## Licence

Ce projet est distribué à des fins éducatives. Utilisez-le à vos propres risques.
