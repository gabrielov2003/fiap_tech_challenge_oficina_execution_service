import os
from datetime import date, datetime
from decimal import Decimal

from flask import Flask
from flask.json.provider import DefaultJSONProvider
from flasgger import Swagger
from flask_jwt_extended import JWTManager

import application
from infrastructure import ExecucaoRepositoryDynamo, MensageriaSQS, MetricasDatadog, para_json
from observabilidade import configurar_logs, registrar_rastreamento
from web import execucao


class JSONProvider(DefaultJSONProvider):
    @staticmethod
    def default(o):
        if isinstance(o, (datetime, date)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return para_json(o)
        return DefaultJSONProvider.default(o)


def injetar_dependencias():
    application.execucao_repo = ExecucaoRepositoryDynamo(os.getenv('TABELA_EXECUCOES', 'oficina-local-execucoes'))
    application.mensageria = MensageriaSQS()
    application.metricas = MetricasDatadog()
    application.filas['comandos'] = os.getenv('FILA_COMANDOS', 'oficina-local-execucao-comandos.fifo')
    application.filas['eventos_os'] = os.getenv('FILA_EVENTOS_OS', 'oficina-local-os-eventos.fifo')


def create_app():
    configurar_logs()

    app = Flask(__name__)
    app.json = JSONProvider(app)
    app.config['SWAGGER'] = {
        'title': 'Execution Service',
        'uiversion': 3,
        'specs_route': '/execucao/apidocs/',
        'static_url_path': '/execucao/flasgger_static',
        'specs': [{
            'endpoint': 'apispec_execucao',
            'route': '/execucao/apispec.json',
            'rule_filter': lambda rule: True,
            'model_filter': lambda tag: True,
        }],
    }
    app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'fiap-soat-key')

    JWTManager(app)
    Swagger(app, template={
        'info': {
            'title': 'Execution Service',
            'version': '4.0',
            'description': (
                'Fila de execução das ordens de serviço: diagnóstico, reparo e finalização. '
                'Use o token do funcionário (POST /api/login) no botão Authorize.'
            )
        },
        'securityDefinitions': {
            'Bearer': {
                'type': 'apiKey',
                'name': 'Authorization',
                'in': 'header',
                'description': 'Informe `Bearer <token>`'
            }
        },
        'security': [{'Bearer': []}]
    })

    injetar_dependencias()
    registrar_rastreamento(app)
    app.register_blueprint(execucao, url_prefix='/execucao')

    return app


if __name__ == '__main__':
    app = create_app()
    app.run(host=os.getenv('FLASK_HOST', '127.0.0.1'), port=5000, debug=os.getenv('FLASK_DEBUG', '0') == '1')
