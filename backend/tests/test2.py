import numpy as np
import pandas as pd


CSV_PATH = "/home/bgpdata/Domeye/backend/tests/afg_as_feature_over_time.csv"

def compute_group_stats(df: pd.DataFrame) -> pd.DataFrame:
	if {"asn", "time", "v4_ip_count", "v6_prefix_count"} - set(df.columns):
		raise ValueError("缺少必要列：asn、time、v4_ip_count、v6_prefix_count")

	df = df.copy()
	df["time"] = pd.to_datetime(df["time"])
	df["asn"] = df["asn"].astype(str)

	def calc_avg(series: pd.Series) -> int:
		return int(round(series.mean())) if not series.empty else 0

	summaries = []
	for asn, group in df.groupby("asn"):
		group = group.sort_values("time").reset_index(drop=True)
		values = group["v4_ip_count"].to_numpy()

		if len(values) == 0:
			continue

		min_val = values.min()
		max_val = values.max()

		# 没有下降，无中断
		if max_val == min_val:
			outage_mask = pd.Series(False, index=group.index)
		else:
			min_indices = np.where(values == min_val)[0]
			start_pos = None
			baseline = None
			for idx in min_indices:
				if idx == 0:
					continue
				prev_max = values[:idx].max()
				if prev_max > min_val:
					start_pos = idx
					baseline = prev_max
					break

			if start_pos is None or baseline is None:
				outage_mask = pd.Series(False, index=group.index)
			else:
				suffix_min = np.minimum.accumulate(values[::-1])[::-1]
				recovery_idx = None
				recovery_threshold = baseline if baseline == min_val else min_val + (baseline - min_val) * 0.05
				for idx in range(start_pos + 1, len(values)):
					subseq_min = suffix_min[idx]
					if values[idx] >= recovery_threshold and subseq_min >= recovery_threshold:
						recovery_idx = idx
						break

				if recovery_idx is None:
					end_pos = len(values) - 1
				else:
					end_pos = max(start_pos, recovery_idx - 1)

				mask_array = np.zeros(len(values), dtype=bool)
				mask_array[start_pos : end_pos + 1] = True
				outage_mask = pd.Series(mask_array, index=group.index)

		outage_section = group[outage_mask]
		normal_section = group[~outage_mask]

		if outage_section.empty:
			normal_v4_avg = calc_avg(normal_section["v4_ip_count"])
			normal_v6_avg = calc_avg(normal_section["v6_prefix_count"])
			outage_v4_avg = normal_v4_avg
			outage_v6_avg = normal_v6_avg
		else:
			outage_v4_avg = calc_avg(outage_section["v4_ip_count"])
			outage_v6_avg = calc_avg(outage_section["v6_prefix_count"])
			normal_v4_avg = calc_avg(normal_section["v4_ip_count"])
			normal_v6_avg = calc_avg(normal_section["v6_prefix_count"])

		v4_change_ratio = (
			(normal_v4_avg - outage_v4_avg) / normal_v4_avg
			if normal_v4_avg
			else 0.0
		)
		v6_change_ratio = (
			(normal_v6_avg - outage_v6_avg) / normal_v6_avg
			if normal_v6_avg
			else 0.0
		)

		v4_change_ratio = round(v4_change_ratio, 2)
		v6_change_ratio = round(v6_change_ratio, 2)

		summaries.append(
			{
				"asn": asn,
				"outage_v4_avg": outage_v4_avg,
				"outage_v6_avg": outage_v6_avg,
				"normal_v4_avg": normal_v4_avg,
				"normal_v6_avg": normal_v6_avg,
				"v4_change_ratio": v4_change_ratio,
				"v6_change_ratio": v6_change_ratio,
			}
		)

	return pd.DataFrame(summaries).sort_values("asn").reset_index(drop=True)


def main() -> None:
	df = pd.read_csv(CSV_PATH)
	stats = compute_group_stats(df)
	output_path = "/home/bgpdata/Domeye/backend/tests/afg_as_feature_summary.csv"
	stats.to_csv(output_path, index=False)
	print(f"结果已保存: {output_path}")


if __name__ == "__main__":
	# df = pd.read_csv(CSV_PATH)
	# print(len(set(df["asn"])))
	main()
