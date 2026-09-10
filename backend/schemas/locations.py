"""Static whitelist of valid Sri Lankan cities/towns used to validate form input."""

SRI_LANKA_CITIES = frozenset(
    name.lower()
    for name in [
        # Western Province
        "Colombo", "Dehiwala", "Mount Lavinia", "Moratuwa", "Sri Jayawardenepura Kotte",
        "Kolonnawa", "Kaduwela", "Homagama", "Maharagama", "Kesbewa", "Kotte",
        "Negombo", "Gampaha", "Ja-Ela", "Wattala", "Kelaniya", "Minuwangoda",
        "Kandana", "Katunayake", "Kadawatha", "Kiribathgoda", "Ragama", "Wattala",
        "Kalutara", "Panadura", "Horana", "Beruwala", "Bandaragama", "Wadduwa",
        "Matugama", "Aluthgama",
        # Central Province
        "Kandy", "Peradeniya", "Gampola", "Nawalapitiya", "Wattegama", "Akurana",
        "Matale", "Dambulla", "Galewela", "Sigiriya",
        "Nuwara Eliya", "Hatton", "Talawakele", "Nanu Oya", "Ginigathhena",
        # Southern Province
        "Galle", "Hikkaduwa", "Ambalangoda", "Elpitiya", "Baddegama", "Karapitiya",
        "Matara", "Weligama", "Akuressa", "Deniyaya", "Kamburupitiya",
        "Hambantota", "Tangalle", "Tissamaharama", "Ambalantota", "Beliatta",
        # Northern Province
        "Jaffna", "Nallur", "Chavakachcheri", "Point Pedro", "Karainagar",
        "Kilinochchi", "Mannar", "Vavuniya", "Mullaitivu",
        # Eastern Province
        "Trincomalee", "Kinniya", "Kantale",
        "Batticaloa", "Kattankudy", "Eravur", "Valachchenai",
        "Ampara", "Kalmunai", "Sammanthurai", "Akkaraipattu", "Dehiattakandiya",
        # North Western Province
        "Kurunegala", "Kuliyapitiya", "Narammala", "Wariyapola", "Nikaweratiya",
        "Puttalam", "Chilaw", "Wennappuwa", "Marawila", "Anamaduwa",
        # North Central Province
        "Anuradhapura", "Kekirawa", "Medawachchiya", "Thambuttegama",
        "Polonnaruwa", "Kaduruwela", "Hingurakgoda", "Medirigiriya",
        # Uva Province
        "Badulla", "Bandarawela", "Haputale", "Welimada", "Mahiyanganaya",
        "Monaragala", "Wellawaya", "Bibile", "Kataragama",
        # Sabaragamuwa Province
        "Ratnapura", "Balangoda", "Embilipitiya", "Pelmadulla", "Kuruwita",
        "Kegalle", "Mawanella", "Warakapola", "Rambukkana", "Ruwanwella",
    ]
)


def is_known_city(value: str) -> bool:
    """Case-insensitive lookup against the Sri Lankan cities/towns whitelist."""
    return value.strip().lower() in SRI_LANKA_CITIES
