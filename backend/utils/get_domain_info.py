"""
获取domain_info中的信息
"""

def get_domain_ip(domain_info: dict, url: str):
    """
    Returns the domain ip for the given URL
    :param domain_info: Domain Information
    :param url: URL
    """
    if url in domain_info and domain_info[url].get('ip') not in [[], None]:
        if domain_info[url].get('ip') == '':
            return ""
        ip_list = eval(domain_info[url].get('ip'))
        if len(ip_list) == 1:
            return ip_list[0]
        elif len(ip_list) > 1:
            # 字符串拼接
            ip_str = ""
            for ip in ip_list:
                if ip != ip_list[-1]:
                    ip_str += ip + '\n'
                else:
                    ip_str += ip
            return ip_str
            
    else:
        return ""

def get_domain_prefix(domain_info: dict, url: str):
    """
    Returns the domain prefix for the given URL
    :param domain_info: Domain Information
    :param url: URL
    """
    if url in domain_info and domain_info[url].get('ip_prefix') not in [[], None]:
        if domain_info[url].get('ip_prefix') == '':
            return ""
        prefix_list = eval(domain_info[url].get('ip_prefix'))
        if len(prefix_list) == 1:
            return prefix_list[0]
        elif len(prefix_list) > 1:
            # 字符串拼接
            prefix_str = ""
            for prefix in prefix_list:
                if prefix != prefix_list[-1]:
                    prefix_str += prefix + '\n'
                else:
                    prefix_str += prefix
            return prefix_str
    else:
        return []
    
def get_domain_industry(domain_info: dict, url: str):
    """
    Returns the domain industry for the given URL
    :param domain_info: Domain Information
    :param url: URL
    """
    if url in domain_info and domain_info[url].get('industry') not in ['', None]:
        return domain_info[url].get('industry')
    else:
        return ''