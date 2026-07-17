"""
获取as_info中的信息
"""

def get_admin_info(as_info: dict, asn: str):
    if '_' in asn:
        public_as = asn.split('_')[0]
        admin_info = eval(as_info[public_as].get('admin_info')) if public_as in as_info and as_info[public_as].get('admin_info') != '' else []
        tech_info = eval(as_info[public_as].get('tech_info')) if public_as in as_info and as_info[public_as].get('tech_info') != '' else []
        abuse_info = eval(as_info[public_as].get('abuse_info')) if public_as in as_info and as_info[public_as].get('abuse_info') != '' else []
    else:
        admin_info = eval(as_info[asn].get('admin_info')) if asn in as_info and as_info[asn].get('admin_info') != '' else []
        tech_info = eval(as_info[asn].get('tech_info')) if asn in as_info and as_info[asn].get('tech_info') != '' else []
        abuse_info = eval(as_info[asn].get('abuse_info')) if asn in as_info and as_info[asn].get('abuse_info') != '' else []
    return admin_info, tech_info, abuse_info

def get_as_country(as_info: dict, asn: str):
    """
    Returns the country to which the autonomous system number asn belongs
    If there is no such asn in the as_info, return None
    :param as_info: Autonomous System Information
    :param asn: Autonomous system number
    :return: A country name or None
    """
    if '_' in asn:
        public_as = asn.split('_')[0]
        return as_info[public_as].get('as_country') if public_as in as_info and as_info[public_as].get('as_country') != '' else ''
    else:
        return as_info[asn].get('as_country') if asn in as_info and as_info[asn].get('as_country') != '' else ''

def get_as_country_cn(as_info: dict, asn: str):
    """
    Returns the country to which the autonomous system number asn belongs
    If there is no such asn in the as_info, return None
    :param as_info: Autonomous System Information
    :param asn: Autonomous system number
    :return: A country name or None
    """
    if '_' in asn:
        public_as = asn.split('_')[0]
        return as_info[public_as].get('as_country_cn') if public_as in as_info and as_info[public_as].get('as_country_cn') != '' else ''
    else:
        return as_info[asn].get('as_country_cn') if asn in as_info and as_info[asn].get('as_country_cn') != '' else ''

def get_as_name(as_info: dict, asn: str):
    """
    Returns the name of the autonomous system numbered asn
    If there is no such asn in the as_info, return None
    :param as_info: Autonomous System Information
    :param asn: Autonomous system number
    :return: An Autonomous System name or None
    """
    if '_' in asn:
        public_as = asn.split('_')[0]
        return as_info[public_as].get('as_name') if public_as in as_info and as_info[public_as].get('as_name') != '' else ''
    else:
        return as_info[asn].get('as_name') if asn in as_info and as_info[asn].get('as_name') != '' else ''

# 如果有中文名称则选择中文名称
def get_as_org_name(as_info: dict, asn: str):
    """
    Returns the name of the organization to which the autonomous system with number asn belongs
    If there is no such asn in the as_info, return None
    :param as_info: Autonomous System Information
    :param asn: Autonomous system number
    :return: A business name or None
    """
    if '_' in asn:
        public_as = asn.split('_')[0]
        if public_as in as_info and as_info[public_as].get('org_name_cn') not in ['', None]:
            return as_info[public_as].get('org_name_cn')
        if public_as in as_info and as_info[public_as].get('org_name') not in ['', None]:
            return as_info[public_as].get('org_name')
        return ''
    else:
        if asn in as_info and as_info[asn].get('org_name_cn') not in ['', None]:
            return as_info[asn].get('org_name_cn')
        if asn in as_info and as_info[asn].get('org_name') not in ['', None]:
            return as_info[asn].get('org_name')
        return ''

# 返回英文名称
def get_as_org_name_en(as_info: dict, asn: str):
    """
    Returns the name of the organization to which the autonomous system with number asn belongs
    If there is no such asn in the as_info, return None
    :param as_info: Autonomous System Information
    :param asn: Autonomous system number
    :return: A business name or None
    """
    if '_' in asn:
        public_as = asn.split('_')[0]
        if public_as in as_info and as_info[public_as].get('org_name') not in ['', None]:
            return as_info[public_as].get('org_name')
        return ''
    else:
        if asn in as_info and as_info[asn].get('org_name') not in ['', None]:
            return as_info[asn].get('org_name')
        return ''
    
def get_global_rank(as_info: dict, asn: str):
    if '_' in asn:
        public_as = asn.split('_')[0]
        if public_as in as_info and as_info[public_as].get('global_rank') not in ['', None]:
            return str(int(float(as_info[public_as].get('global_rank'))))
        return ''
    else:
        if asn in as_info and as_info[asn].get('global_rank') not in ['', None]:
            return str(int(float(as_info[asn].get('global_rank'))))
        return ''
    
def get_country_rank(as_info: dict, asn: str):
    if '_' in asn:
        public_as = asn.split('_')[0]
        if public_as in as_info and as_info[public_as].get('country_rank') not in ['', None]:
            return str(int(float(as_info[public_as].get('country_rank'))))
        return ''
    else:
        if asn in as_info and as_info[asn].get('country_rank') not in ['', None]:
            return str(int(float(as_info[asn].get('country_rank'))))
        return ''
    
def get_as_type(as_info: dict, asn: str):
    if '_' in asn:
        public_as = asn.split('_')[0]
        if public_as in as_info and as_info[public_as].get('type_cn') not in ['', None]:
            return as_info[public_as].get('type_cn')
        if public_as in as_info and as_info[public_as].get('type') not in ['', None]:
            return as_info[public_as].get('type')
        return ''
    else:
        if asn in as_info and as_info[asn].get('type_cn') not in ['', None]:
            return as_info[asn].get('type_cn')
        if asn in as_info and as_info[asn].get('type') not in ['', None]:
            return as_info[asn].get('type')
        return ''
    
def get_as_info(as_info: dict, asn: str):
    """
    根据asn,as名,as机构,as国家等信息组成一段文本
    :param asn: _description_
    :param as_name: _description_
    :param as_org: _description_
    :param as_country: _description_
    :return: as信息
        1. AS asn   三个字段都为none
        2. AS asn (as_name) or AS asn (as_org) or AS asn (as_country)  其中一个非none
        3. AS asn (as_name, as_org) or AS asn (as_name, as_country) or AS asn (as_org, as_country)
        4. AS asn (as_name, as_org, as_country)
    """    
    if '_' in asn:
        public_as = asn.split('_')[0]
        # print(as_info[public_as])
        if public_as in as_info and as_info[public_as].get('as_info') not in ['', None]:
            # print("as_info：", as_info[public_as].get('as_info', ""))
            return as_info[public_as].get('as_info', "")
        return ""
    else:
        # print(as_info[asn])
        if asn in as_info and as_info[asn].get('as_info') not in ['', None]:
            # print("as_info：", as_info[asn].get('as_info'))
            return as_info[asn].get('as_info')
        return ""
    
def get_as_descr(as_info: dict, asn: str):
    if '_' in asn:
        public_as = asn.split('_')[0]
        if public_as in as_info and as_info[public_as].get('descr_cn') != '':
            return as_info[public_as].get('descr_cn')
        if public_as in as_info and as_info[public_as].get('descr') != '':
            return as_info[public_as].get('descr')
        return ''
    else:
        if asn in as_info and as_info[asn].get('descr_cn') != None:
            return as_info[asn].get('descr_cn')
        if asn in as_info and as_info[asn].get('descr') != '':
            return as_info[asn].get('descr')
        return ''

def get_as_admin(as_info: dict, asn: str):
    if '_' in asn:
        public_as = asn.split('_')[0]
        return as_info[public_as].get('admin_info') if public_as in as_info and as_info[public_as].get('admin_info') != '' else ''
    else:
        return as_info[asn].get('admin_info') if asn in as_info and as_info[asn].get('admin_info') != '' else ''

