from flask import request
from flask_restful import Resource

from services import get_node_status_list


class NodeStatusListResource(Resource):
    def get(self):
        return get_node_status_list(
            asn=request.args.get('asn', ''),
            page_num=request.args.get('page_num'),
            page_size=request.args.get('page_size'),
        )
