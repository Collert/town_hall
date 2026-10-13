"""Starter skills offered at Console > Organization > Skills, in every supported language."""
from django.conf import settings
from django.db import transaction
from modeltranslation.utils import build_localized_fieldname

from .models import Skill

# One dict per skill: language code -> name. Keys match settings.SUPPORTED_LANGUAGES.
DEFAULT_SKILLS = [
    {'en': 'First Aid & CPR', 'es': 'Primeros auxilios y RCP', 'fr': 'Premiers soins et RCR',
     'uk': 'Перша допомога та СЛР', 'zh-hant': '急救與心肺復甦術', 'tl': 'First Aid at CPR'},
    {'en': 'Food Handling', 'es': 'Manipulación de alimentos', 'fr': 'Manipulation des aliments',
     'uk': 'Робота з харчовими продуктами', 'zh-hant': '食品處理', 'tl': 'Paghawak ng Pagkain'},
    {'en': 'Cooking', 'es': 'Cocina', 'fr': 'Cuisine',
     'uk': 'Приготування їжі', 'zh-hant': '烹飪', 'tl': 'Pagluluto'},
    {'en': 'Event Setup & Teardown', 'es': 'Montaje y desmontaje de eventos', 'fr': "Montage et démontage d'événements",
     'uk': 'Підготовка та прибирання заходів', 'zh-hant': '活動場地佈置與拆卸', 'tl': 'Pag-aayos at Pagliligpit ng Event'},
    {'en': 'Customer Service', 'es': 'Atención al público', 'fr': 'Service à la clientèle',
     'uk': 'Обслуговування відвідувачів', 'zh-hant': '客戶服務', 'tl': 'Serbisyo sa Kostumer'},
    {'en': 'Cash Handling', 'es': 'Manejo de efectivo', 'fr': "Manipulation d'argent",
     'uk': 'Робота з готівкою', 'zh-hant': '現金處理', 'tl': 'Paghawak ng Pera'},
    {'en': 'Driving', 'es': 'Conducción', 'fr': 'Conduite',
     'uk': 'Водіння', 'zh-hant': '駕駛', 'tl': 'Pagmamaneho'},
    {'en': 'Childcare', 'es': 'Cuidado infantil', 'fr': "Garde d'enfants",
     'uk': 'Догляд за дітьми', 'zh-hant': '兒童照顧', 'tl': 'Pag-aalaga ng Bata'},
    {'en': 'Senior Support', 'es': 'Acompañamiento a personas mayores', 'fr': 'Soutien aux aînés',
     'uk': 'Підтримка літніх людей', 'zh-hant': '長者關懷', 'tl': 'Pag-aalaga sa Nakatatanda'},
    {'en': 'Tutoring', 'es': 'Tutoría', 'fr': 'Tutorat',
     'uk': 'Репетиторство', 'zh-hant': '課業輔導', 'tl': 'Pagtuturo'},
    {'en': 'Mentoring', 'es': 'Mentoría', 'fr': 'Mentorat',
     'uk': 'Наставництво', 'zh-hant': '導師輔導', 'tl': 'Pagmementor'},
    {'en': 'Interpreting & Translation', 'es': 'Interpretación y traducción', 'fr': 'Interprétation et traduction',
     'uk': 'Усний і письмовий переклад', 'zh-hant': '口譯與筆譯', 'tl': 'Pagsasalin at Interpretasyon'},
    {'en': 'Sign Language', 'es': 'Lengua de señas', 'fr': 'Langue des signes',
     'uk': 'Жестова мова', 'zh-hant': '手語', 'tl': 'Sign Language'},
    {'en': 'Public Speaking', 'es': 'Hablar en público', 'fr': 'Prise de parole en public',
     'uk': 'Публічні виступи', 'zh-hant': '公開演講', 'tl': 'Pagsasalita sa Publiko'},
    {'en': 'Team Leadership', 'es': 'Liderazgo de equipos', 'fr': "Leadership d'équipe",
     'uk': 'Керування командою', 'zh-hant': '團隊領導', 'tl': 'Pamumuno ng Grupo'},
    {'en': 'Crowd Management', 'es': 'Control de multitudes', 'fr': 'Gestion des foules',
     'uk': 'Керування потоками людей', 'zh-hant': '人群管理', 'tl': 'Pamamahala ng Maraming Tao'},
    {'en': 'Fundraising', 'es': 'Recaudación de fondos', 'fr': 'Collecte de fonds',
     'uk': 'Збір коштів', 'zh-hant': '募款', 'tl': 'Pangangalap ng Pondo'},
    {'en': 'Social Media', 'es': 'Redes sociales', 'fr': 'Médias sociaux',
     'uk': 'Соціальні мережі', 'zh-hant': '社群媒體', 'tl': 'Social Media'},
    {'en': 'Photography', 'es': 'Fotografía', 'fr': 'Photographie',
     'uk': 'Фотографія', 'zh-hant': '攝影', 'tl': 'Potograpiya'},
    {'en': 'Graphic Design', 'es': 'Diseño gráfico', 'fr': 'Design graphique',
     'uk': 'Графічний дизайн', 'zh-hant': '平面設計', 'tl': 'Graphic Design'},
    {'en': 'Audio & Video Setup', 'es': 'Montaje de audio y video', 'fr': 'Installation audio-vidéo',
     'uk': 'Налаштування звуку й відео', 'zh-hant': '音響與影音設備架設', 'tl': 'Pag-set up ng Audio at Video'},
    {'en': 'Data Entry', 'es': 'Captura de datos', 'fr': 'Saisie de données',
     'uk': 'Введення даних', 'zh-hant': '資料輸入', 'tl': 'Pag-encode ng Datos'},
    {'en': 'Office Administration', 'es': 'Administración de oficina', 'fr': 'Travail de bureau',
     'uk': 'Офісна робота', 'zh-hant': '行政事務', 'tl': 'Gawaing Pang-opisina'},
    {'en': 'Inventory & Logistics', 'es': 'Inventario y logística', 'fr': 'Inventaire et logistique',
     'uk': 'Облік і логістика', 'zh-hant': '庫存與物流', 'tl': 'Imbentaryo at Lohistika'},
    {'en': 'Cleaning & Sanitation', 'es': 'Limpieza y desinfección', 'fr': 'Nettoyage et désinfection',
     'uk': 'Прибирання та санітарія', 'zh-hant': '清潔與消毒', 'tl': 'Paglilinis at Sanitasyon'},
    {'en': 'Gardening', 'es': 'Jardinería', 'fr': 'Jardinage',
     'uk': 'Садівництво', 'zh-hant': '園藝', 'tl': 'Paghahalaman'},
    {'en': 'Repairs & Maintenance', 'es': 'Reparaciones y mantenimiento', 'fr': 'Réparations et entretien',
     'uk': 'Ремонт і обслуговування', 'zh-hant': '維修保養', 'tl': 'Pagkukumpuni at Pagpapanatili'},
]


def add_default_skills():
    """Create every default skill whose name isn't taken in any language yet. Returns how many were added."""
    columns = [build_localized_fieldname('name', code) for code in settings.MODELTRANSLATION_LANGUAGES]
    taken = {name.strip().casefold() for row in Skill.objects.values_list(*columns) for name in row if name}
    added = 0
    with transaction.atomic():
        for names in DEFAULT_SKILLS:
            if any(name.casefold() in taken for name in names.values()):
                continue
            Skill.objects.create(**{build_localized_fieldname('name', code): name for code, name in names.items()})
            added += 1
    return added
