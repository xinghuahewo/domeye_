"""
获取prefix_info中的信息
"""

def get_prefix_name(prefix_info: dict, prefix: str): 
    if prefix in prefix_info:
        return prefix_info[prefix].get('name') 
    else:
        return ''

def get_prefix_domain_num(prefix_info: dict, prefix: str): 
    if prefix in prefix_info:
        return prefix_info[prefix].get('domain_num') 
    else:
        return 0

def get_prefix_domain_auth_num(prefix_info: dict, prefix: str): 
    if prefix in prefix_info:
        return prefix_info[prefix].get('domain_auth_num') 
    else:
        return 0

def get_prefix_domain(prefix_info: dict, prefix: str): 
    if prefix in prefix_info and prefix_info[prefix].get('domain') not in ['', None]:
        return eval(prefix_info[prefix].get('domain')) 
    else:
        return []

def get_prefix_domain_auth(prefix_info: dict, prefix: str): 
    if prefix in prefix_info and prefix_info[prefix].get('domain_auth') not in ['', None]:
        return eval(prefix_info[prefix].get('domain_auth')) 
    else:
        return []