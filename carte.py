import json
import urllib.request
from collections import Counter
from pathlib import Path
import webbrowser

import pandas as pd
import plotly.colors as pc
import plotly.graph_objects as go

import os           # pour sauver la carte en html

# ---------- Réglages ----------
BASE = "https://s5.fourmizzz.fr/api/exports"   # serveur S5 (HTTPS)
TOP_N = 15                                       # nombre d'alliances avec leur propre couleur
MON_TAG = "CUIR"                  # tag de ton alliance : pseudos affichés sur la carte
TAILLE_MAX = 30                                  # diamètre du plus gros rond, en pixels
CACHE_DIR = Path("cache")
SORTIE = Path("carte_royaume.html")
DECALAGE = 0.5   # distance du texte par rapport au rond, en unités de coordonnées

ROLES_CSV = Path("csvs/roles.csv")
SECTEURS_CSV = Path("csvs/secteurs.csv")
SYMBOLES = {"chasseur": "triangle-up",
            "passeur": "diamond",
            "grenier": "circle",
            "sans rôle": "square"
            }

SECTEURS_COULEURS = [
    (31, 119, 180),   # bleu
    (255, 127, 14),   # orange
    (44, 160, 44),    # vert
    (214, 39, 40),    # rouge
    (148, 103, 189),  # violet
    (140, 86, 75),    # brun
    (227, 119, 194),  # rose
    (23, 190, 207),   # cyan
]
TRANSPARENCE_SECTEUR = 0.15   # 0 = invisible, 1 = opaque

SORTIE = Path("carte/index.html")       # sauvegarde de la carte en html

# ------------------------------

CACHE_DIR.mkdir(exist_ok=True)


def get_json(url):
    with urllib.request.urlopen(url, timeout=30) as reponse:
        return json.load(reponse)


def charger_joueurs():
    """Télécharge la dernière version des joueurs, ou la lit dans le cache."""
    versions = get_json(f"{BASE}/")["players"]
    if not versions:
        raise SystemExit("Aucun export de joueurs disponible sur ce serveur.")

    derniere = versions[0]
    fichier = CACHE_DIR / f"players_{derniere}.json"

    if fichier.exists():
        print(f"Version {derniere} déjà en cache.")
        return json.loads(fichier.read_text(encoding="utf-8"))

    print(f"Téléchargement de la version {derniere}...")
    joueurs = get_json(f"{BASE}/players/?version={derniere}")
    fichier.write_text(json.dumps(joueurs, ensure_ascii=False), encoding="utf-8")
    return joueurs


def preparer_donnees(joueurs):
    """Transforme la liste brute en tableau avec un groupe de couleur par joueur."""
    compte = Counter(j["alliance"] for j in joueurs if j["alliance"])
    top = [tag for tag, _ in compte.most_common(TOP_N)]

    lignes = []
    for j in joueurs:
        tag = j["alliance"]
        if tag is None:
            groupe = "Sans alliance"
        elif tag in top:
            groupe = tag
        else:
            groupe = "Autres"

        lignes.append({
            "x": j["x"],
            "y": j["y"],
            "pseudo": j["pseudo"],
            "groupe": groupe,
            "alliance": tag or "-",
            "terrain": j["field"],
            "score_batiments": j["buildingScore"],
            "score_technologies": j["technologyScore"],
            "score_trophees": j["trophyScore"],
        })
    return pd.DataFrame(lignes), top

def ajouter_roles(df):
    """Ajoute rôle, secteur et symbole aux membres de MON_TAG, à partir de roles.csv."""
    df = df.copy()
    df["role"] = ""
    df["secteur"] = ""

    if ROLES_CSV.exists():
        roles = pd.read_csv(ROLES_CSV, sep=";", encoding="utf-8-sig", dtype=str).fillna("")
        roles["role"] = roles["role"].str.strip()
        roles["secteur"] = roles["secteur"].str.strip()
        dico = roles.drop_duplicates("pseudo").set_index("pseudo")

        mask = df["alliance"] == MON_TAG
        df.loc[mask, "role"] = df.loc[mask, "pseudo"].map(dico["role"]).fillna("")
        df.loc[mask, "secteur"] = df.loc[mask, "pseudo"].map(dico["secteur"]).fillna("")

    mask = df["alliance"] == MON_TAG
    df.loc[mask & (df["role"] == ""), "role"] = "Sans rôle"

    # Rôle inconnu (faute de frappe, casse différente) : signalé dans le terminal
    inconnus = df.loc[mask & ~df["role"].isin(SYMBOLES.keys()), ["pseudo", "role"]]
    for _, r in inconnus.iterrows():
        print(f"Rôle non reconnu pour {r['pseudo']} : '{r['role']}'")

    df["symbole"] = df["role"].map(SYMBOLES).fillna("circle")
    return df


def afficher_sans_role(df):
    """Affiche la liste des membres de MON_TAG sans rôle défini."""
    sans = df[(df["alliance"] == MON_TAG) & (df["role"] == "Sans rôle")]
    print(f"\n{len(sans)} membres sans rôle :")
    for pseudo in sorted(sans["pseudo"]):
        print(f"  - {pseudo}")
    print()


def dessiner_secteurs(fig):
    """Colorie les secteurs définis dans secteurs.csv, chacun avec une couleur presque transparente."""
    if not SECTEURS_CSV.exists():
        return
    secteurs = pd.read_csv(SECTEURS_CSV, sep=";", encoding="utf-8-sig")
    for i, (_, s) in enumerate(secteurs.iterrows()):
        r, g, b = SECTEURS_COULEURS[i % len(SECTEURS_COULEURS)]
        fig.add_shape(
            type="rect",
            x0=s["x_min"], x1=s["x_max"],
            y0=s["y_min"], y1=s["y_max"],
            line=dict(width=0), #line=dict(color=f"rgba({r},{g},{b},0.5)", width=1),
            fillcolor=f"rgba({r},{g},{b},{TRANSPARENCE_SECTEUR})",
            layer="below",
        )
        fig.add_annotation(
            x=s["x_min"], y=s["y_max"], text=f"<b>{s['nom']}</b>",
            showarrow=False, xanchor="left", yanchor="bottom",
            font=dict(size=14, color=f"rgb({r},{g},{b})"),
        )

def creer_carte(df, top):
    # Couleurs : une par alliance du top, gris pour les autres
    palette = pc.qualitative.Dark24
    couleurs = {"Sans alliance": "#9e9e9e", "Autres": "#d0d0d0"}
    for i, tag in enumerate(top):
        couleurs[tag] = palette[i % len(palette)]

    # Diamètre proportionnel à la racine du terrain, donc surface proportionnelle au terrain
    max_terrain = max(df["terrain"].max(), 1)
    df = df.copy()
    df["coord"] = df.apply(lambda r: f"({r['x']}, {r['y']})", axis=1)
    df["diametre"] = (TAILLE_MAX * (df["terrain"] / max_terrain) ** 0.5).clip(lower=3)

    fig = go.Figure()

    # Couche 1 : les ronds, une trace par groupe (pour la légende), avec survol
    groupes = [g for g in top + ["Autres", "Sans alliance"] if g in set(df["groupe"])]
    for groupe in groupes:
        sub = df[df["groupe"] == groupe]
        fig.add_trace(go.Scatter(
            x=sub["x"],
            y=sub["y"],
            mode="markers",
            name=groupe,
            marker=dict(size=sub["diametre"], color=couleurs[groupe],
                        symbol = sub["symbole"],
                        opacity=0.7, line=dict(width=0)),
            customdata=sub[["pseudo", "alliance", "terrain", "score_batiments",
                            "score_technologies", "score_trophees", "coord"]].to_numpy(),
            hovertemplate=(
                "<b>%{customdata[0]}</b><br>"
                "%{customdata[6]}<br>"
                "Alliance : %{customdata[1]}<br>"
                "Terrain : %{customdata[2]:,}<br>"
                "Bâtiments : %{customdata[3]}<br>"
                "Technologies : %{customdata[4]}<br>"
                "Trophées : %{customdata[5]}"
                "<extra></extra>"
            ),
        ))

    # Couche 2 : pseudos de mon alliance uniquement, au-dessus à droite du rond
    membres = df[df["alliance"] == MON_TAG]
    print(f"{len(membres)} membres trouvés pour {MON_TAG}")
    if not membres.empty:
        fig.add_trace(go.Scatter(
            x=membres["x"] + DECALAGE,
            y=membres["y"] + 0 * DECALAGE,
            mode="text",
            text=membres["pseudo"],
            textposition="top right",
            textfont=dict(size=12, color="red"),
            hoverinfo="skip",
            showlegend=False,
        ))

    # Légende des rôles (traces factices, sans points)
    for nom, forme in SYMBOLES.items():
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers", name=f"{nom}",
            marker=dict(symbol=forme, size=12, color="black"),
        ))

    dessiner_secteurs(fig)

    fig.update_layout(
        title="Carte du royaume (S5)",
        height=900,
        hovermode="closest",
        plot_bgcolor="white",
        legend_title_text="Alliance",
        xaxis=dict(title="X", range=[-10, 105], autorange=False, constrain="domain"),
        yaxis=dict(title="Y", autorange=True),
    )
    fig.update_yaxes(scaleanchor="x", scaleratio=1)
    return fig

def exporter_modele(joueurs):
    """Crée roles_modele.csv avec tous les membres de MON_TAG, à remplir à la main."""
    membres = sorted(j["pseudo"] for j in joueurs if j["alliance"] == MON_TAG)
    modele = pd.DataFrame({"pseudo": membres, "role": "", "secteur": ""})
    modele.to_csv("csvs/roles_modele_new.csv", sep=";", index=False, encoding="utf-8-sig")
    print(f"Modèle créé : roles_modele.csv ({len(membres)} membres)")
    
def main():
    joueurs = charger_joueurs()
    
    print(f"{len(joueurs)} joueurs chargés.")

    # grades = sorted({str(j["grade"]) for j in joueurs if j["alliance"] == MON_TAG})
    # print("Grades dans CUIR :", grades)

    # exporter_modele(joueurs)

    df, top = preparer_donnees(joueurs)
    df = ajouter_roles(df)
    afficher_sans_role(df)
    fig = creer_carte(df, top)

    fig.write_html(SORTIE, include_plotlyjs=True)
    print(f"Carte générée : {SORTIE.resolve()}")
    
    if not os.environ.get("GITHUB_ACTIONS"):   # ouvre le navigateur seulement en local
        webbrowser.open(SORTIE.resolve().as_uri())


if __name__ == "__main__":
    main()


