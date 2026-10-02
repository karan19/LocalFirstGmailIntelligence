"""Synthetic local smoke check: no Gmail access, product run or learning writes."""
import json
from app.services import Classifier
from app.winnow import WinnowClassifier

messages = [
    {'sender':'billing@example.com','subject':'Overdue payment requires action','text':'Your payment is overdue. Please resolve the unpaid balance by tomorrow.'},
    {'sender':'accounts@example.com','subject':'Your one-time verification code','text':'Use this one-time code to verify your sign-in. It expires in ten minutes.'},
]
laya, winnow = Classifier(), WinnowClassifier()
for message in messages:
    results = {}
    for name, classifier in (('laya',laya),('winnow',winnow)):
        result = classifier.classify(message)
        results[name] = {k:result.get(k) for k in ('decision','reason_basis','confidence','elapsed_seconds')}
    print(json.dumps({'subject':message['subject'],'models':results}))
