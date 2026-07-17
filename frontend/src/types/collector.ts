export interface CollectorOption {
  id: string;
  label: string;
  alias: string;
  collectorParam: string | number;
}

export interface CollectorContextState {
  collectorOptions: CollectorOption[];
  activeCollectorId: string;
}

export interface VantagePointState {
  collector?: string;
  time?: string;
  is_outlier?: boolean;
  ipv4_prefix_count?: number | string;
  ipv6_prefix_count?: number | string;
  ipv4_address_count?: number | string;
  ipv6_48_count?: number | string;
  vp_count?: number | string;
  private_as_count?: number | string;
  path_count?: number | string;
  public_as_count?: number | string;
  ipv4_prefix_normal_upper?: number | string;
  ipv4_prefix_normal_lower?: number | string;
  ipv6_prefix_normal_upper?: number | string;
  ipv6_prefix_normal_lower?: number | string;
  private_as_normal_upper?: number | string;
  private_as_normal_lower?: number | string;
  path_normal_upper?: number | string;
  path_normal_lower?: number | string;
  public_as_normal_upper?: number | string;
  public_as_normal_lower?: number | string;
}

export interface HomeEventRow {
  detail_url?: string;
  event_type?: string;
  affected_prefix?: string;
  attacked_as?: string;
  attacked_org?: string;
  attacked_country?: string;
  attacker_as?: string;
  attacker_org?: string;
  attacker_country?: string;
  level?: string;
  start_time?: string;
  end_time?: string;
}
