import mongoengine
from data.case import Case

class Cases(mongoengine.Document):
    _id   = mongoengine.IntField(required=True)
    cases = mongoengine.EmbeddedDocumentListField(Case, default=[])
    meta = {
        'db_alias': 'core',
        'collection': 'cases'
    }