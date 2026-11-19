# Déploiement Render — eCampus BBT

## 1. Première mise en ligne
1. Pousser le dépôt sur GitHub.
2. Render → **New → Blueprint** → choisir le dépôt (lit `render.yaml`).
3. Renseigner les variables `sync: false` (clés KkiaPay / Flutterwave, SMTP).
4. Attendre le premier déploiement (build → migrations → démarrage).
5. Shell du service web : `python manage.py createsuperuser` (le rôle `admin` est posé automatiquement).

## 2. Données à créer dans `/admin/` (rien n'est préchargé)
- **PaymentPlan** : sans plan actif, le formulaire d'inscription est vide.
- **Notions** de chaque niveau (`order` = 1, 2, 3… sans trou).
- **Un Quiz par notion** (questions + choix, au moins un `is_correct`) : un élève ne
  termine une notion qu'en réussissant son quiz — sans quiz, la progression se bloque.
- **PDF / audios** sur chaque notion (stockés sur le disque persistant `/var/data/media`).

## 3. Prestataires de paiement (URLs à enregistrer côté dashboard)
- Flutterwave webhook : `https://<domaine>/payments/webhook/flutterwave/` + recopier le *Secret Hash* dans `FLUTTERWAVE_SECRET_HASH`.
- KkiaPay (Dashboard → Développeurs) : recopier les **3 clés** (public, private, secret) ;
  ajouter le webhook `https://<domaine>/payments/webhook/kkiapay/` avec un *hash secret*
  recopié dans `KKIAPAY_WEBHOOK_SECRET`. Garder `KKIAPAY_SANDBOX=1` pour les tests
  (numéros de test : guide Sandbox KkiaPay), puis passer à `0` en production.
- Flux KkiaPay : checkout → page widget JS → transactionId → vérification serveur
  (`/api/v1/transactions/status`) → déblocage. Le webhook est re-vérifié, jamais cru.
- Tester un paiement réel de bout en bout (sandbox puis petit montant réel) avant d'ouvrir.

## 4. Vérifications après déploiement
- `https://<domaine>/healthz/` → `ok`
- Inscription test → paiement → déverrouillage de la 1ʳᵉ notion → e-mail de confirmation reçu.
- Un PDF et un audio s'ouvrent (seek audio OK sur iPhone).
- Logs Render : aucun `Payment … NOT confirmed` ni `replay refused` inattendu.

## 5. Points d'attention
- Plan **starter** minimum (web + cron) : le Free dort après 15 min, perd ses fichiers et bloque le SMTP.
- Un disque persistant limite le service à 1 instance et désactive les déploiements sans coupure.
- Montée HSTS : `SECURE_HSTS_SECONDS` 3600 → 31536000 une fois le HTTPS validé.
- Rate limiting en cache local (par worker) : suffisant anti-brute-force ; cache partagé pour un plafond strict.
