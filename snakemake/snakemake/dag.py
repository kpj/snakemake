from collections import defaultdict
from itertools import chain, combinations
from functools import partial
from operator import itemgetter

from snakemake.jobs import Job

class DAG:
	def __init__(self, 
	             workflow,
	             targetfiles = None, 
	             targetrules = None,
	             forceall = False,
	             forcetargets = False, 
	             forcerules = None,
	             ignore_ambiguity = False):
		self.dependencies = defaultdict(partial(defaultdict, list))
		self.depending = defaultdict(partial(defaultdict, list))
		self._len = None
		self.workflow = workflow
		self.rules = workflow.rules
		self.targetfiles = targetfiles
		self.targetrules = targetrules
		self.forcerules = set()
		if forceall:
			self.forcerules = self.rules
		elif forcerules:
			self.forcerules = forcerules
		if forcetargets:
			self.forcerules.update(targetrules)
		if ignore_ambiguity:
			self.select_dependency = self.select_dependency_ign_amb
		

		self.targetjobs = list(map(self.rule2job, self.targetrules))
		exceptions = defaultdict(list)		
		for file in self.targetfiles:
			try:
				for job in self.file2job(file):
					self.targetjobs.append(job)
			except MissingRuleException as ex:
				exceptions[file].append(ex)
		has_producer = set()
		for job in self.targetjobs:
			try:
				self.update(job)
				has_producer.add(job.targetfile)
			except MissingInputException as ex:
				exceptions[job.targetfile].append(ex)
		for file in has_producer:
			exceptions.remove(file)
		if exceptions:
			raise RuleException(include=exceptions.values())

	@property
	def jobs(self):
		for job in self.bfs(self.dependencies, *self.targetjobs):
			yield job
		
	def update(self, job, visited = None, parentmintime = None, skip_until_dynamic = False):
		if job in self.dependencies:
			return
		if visited is None:
			visited = set(job)
		dependencies = self.dependencies[job]
		potential_dependencies = self.collect_potential_dependencies(job).items()

		output_mintime = IOFile.mintime(job.output) or parentmintime
		skip_until_dynamic = not job.dynamic_output and (skip_until_dynamic or job.dynamic_input)
			
		missing_input = job.missing_input
		exceptions = list()
		for file, jobs in potential_dependencies.items():
			try:
				job_ = self.select_dependency(file, jobs, visited)
				self.update(job_, visited=set(visited), parentmintime=output_mintime, skip_until_dynamic=skip_until_dynamic)
				missing_input.remove(file)
				# TODO check for pumping up wildcards...
				if not skip_until_dynamic:
					dependencies[job_].append(file)
					self.depending[job_][job].append(file)
			except RuleException as ex:
				exceptions.append(ex)
		if missing_input:
			raise MissingInputFileException(job.rule, missing_input)
			
		job.needrun, job.reason = self.needrun(self, job, output_mintime)
		if needrun:
			self.update_needrun_temp(job)
	
	def needrun(self, job, output_mintime):
		# forced?
		if job.rule in self.forcerules or (forcetargets and job in self.targetjobs):
			return True, "Forced execution"
			
		# missing output?
		output_missing = [f for f in job.output if not f.exists()]
		if output_missing:
			return True, "Missing output files: {}".format(", ".join(output_missing))
			
		# updated input?
		updated_input = set(f for job_, f in self.dependencies[job] if job_.needrun)
		for f in job.input:
			if f.exists() and f.is_newer(output_mintime):
				updated_input.add(f)
		if updated_input:
			return True, "Updated input files: {}".format(", ".join(updated_input))
			
		return False, None
	
	def dynamic_update(self, job):
		self._len = None
		dynamic_wildcards = job.dynamic_wildcards
		if dynamic_wildcards:
			for job_ in self.bfs(self.depending, job):
				if not job_.finished and job_.dynamic_input:
					if job_.rule.dynamic_update(dynamic_wildcards):
						self.delete_job(job_)
						self.update(Job(rule, targetfile=job_.targetfile))
	
	def delete_job(self, job):
		for job_ in self.depending[job]:
			del self.dependencies[job_][job]
		del self.depending[job]
		for job_ in self.dependencies[job]:
			depending = self.depending[job_]
			del depending[job]
			if not depending:
				self.delete_job(job_)
		del self.dependencies[job]
	
	def update_needrun_temp(self, job)
		for job_, files in self.dependencies[job].items():
			for f in files:
				if f in job_.temp_output and not f.exists():
					job_.needrun, job_.reason = True, "Missing temp files for rule {}".format(job)
					self.update_needrun_temp(job_)
	
	def collect_potential_dependencies(self, job):
		file2jobs = partial(file2jobs, self.rules)
		dependencies = defaultdict(list)
		for file in job.input:
			for job_ in file2jobs(file):
				dependencies[file].append(job_)
		return dependencies

	def select_dependency(self, file, jobs, visited):
		jobs = sorted(jobs)
		for i, job in reversed(enumerate(jobs)):
			if job not in visited and i > 0 and job > jobs[i-1]:
				return job
			else:
				raise AmbiguousRuleException(file, job.rule, jobs[i-1].rule)
		raise CyclicGraphException()
	
	def select_dependency_ign_amb(self, file, jobs, visited):
		for job in jobs:
			if job not in visited:
				return job
		raise CyclicGraphException(job.rule, file)
	
	def bfs(self, direction, *jobs):
		queue = list(jobs)
		visited = set()
		while queue:
			job = queue.pop(0)
			yield job
			for job_, _ in direction[job]:
				if not job_ in visited:
					queue.append(job_)
					visited.add(job_)

	def new_wildcards(self, job):
		new_wildcards = set(job.wildcards.items())
		for job_ in self.dependencies[job]:
			if not new_wildcards:
				return set()
			for wildcard in job_.wildcards.items():
				new_wildcards.discard(wildcard)
		return new_wildcards

	def __str__(self):
		jobid = dict((job, i) for i, job in enumerate(self.jobs))
		nodes, edges = list(), list()
		for job in self.jobs:
			label = "\n".join([job.rule.name] + list(map(": ".join, self.new_wildcards(job))))
			nodes.append('{}[label = "{}"];'.format(jobid[job], label))
			for job_ in self.dependencies[job]:
				edges.append("{} -> {};".format(jobid[job_], jobid[job])
		
		return textwrap.dedent("""
		                    digraph snakemake_dag {
		                    	node[shape=Msquare];
		                    	{nodes}
		                    	{edges}
		                    }
		                    """.format(nodes=nodes, edges=edges)

	def __len__(self):
		if self._len is None:
			self._len = sum(1 for job in self.jobs)
		return self._len
	
	@lru_cache()
	def rule2job(targetrule):
		return Job(rule=targetrule)
	
	@lru_cache()
	def file2jobs(targetfile):
		jobs = list()
		for rule in self.rules:
			if rule.is_producer(targetfile):
				jobs.append(Job(rule, targetfile=targetfile))
		if not jobs:
			raise MissingRuleException(targetfile)
		return jobs
