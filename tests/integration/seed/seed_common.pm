package seed_common;

# Shared helpers for the integration-test upstream.
#
# Used by generate_lists.pl (creates the initial set of inboxes) and by
# mutate.pl (adds lists and messages to a running upstream).  Keeping the
# inbox layout, message format and refs/meta/origins handling in one place
# keeps both scripts from drifting apart.

use strict;
use warnings;
use v5.10.1;
use Exporter 'import';
use File::Path qw(make_path remove_tree);
use File::Spec;
use Cwd ();
use IPC::Open2 qw(open2);

our @EXPORT_OK = qw(
	init_git_identity make_eml origins_blob write_origins_ref create_list
	write_upstream_config append_inbox_config with_scratch reload_netd
	netd_pids message_count
);

#: Deterministic dates keep inbox fingerprints stable between runs; "new"
#: messages are dated later so they sort to the top of the served index.
my %DATE = (
	original => 'Fri, 01 Jan 2021 00:00:00 +0000',
	new => 'Sat, 01 Jan 2022 00:00:00 +0000',
);

sub init_git_identity {
	for my $var (qw(AUTHOR COMMITTER)) {
		$ENV{"GIT_${var}_NAME"} = 'Integration Test';
		$ENV{"GIT_${var}_EMAIL"} = 'integration@integration.test';
		$ENV{"GIT_${var}_DATE"} = '2021-01-01T00:00:00+0000';
	}
}

#: A synthetic message for one list.
#:
#: kind=original -> "list_0 message 0"  (the baseline set)
#: kind=new      -> "list_0 new 0"      (appended after the first indexing)
sub make_eml {
	my (%arg) = @_;
	my $name = $arg{name};
	my $n = $arg{index};
	my $kind = $arg{kind} // 'original';
	die "unknown message kind '$kind'\n" unless exists $DATE{$kind};

	my $subject = $kind eq 'original' ? "$name message $n" : "$name new $n";
	my $mid = $kind eq 'original' ? "$name-$n" : "$name-new$n";
	my $raw = <<"EOM";
From: sender-$name-$n\@integration.test
To: $name\@integration.test
Cc: $name\@integration.test
Date: $DATE{$kind}
Subject: $subject
Message-Id: <$mid\@integration.test>
List-Id: $name.integration.test
MIME-Version: 1.0
Content-Type: text/plain; charset=utf-8

integration test body for $subject
EOM
	require PublicInbox::Eml;
	return PublicInbox::Eml->new($raw);
}

#: Number of messages currently stored in an inbox (via its msgmap).
sub message_count {
	my ($dir) = @_;
	require PublicInbox::Msgmap;
	my $mm = PublicInbox::Msgmap->new($dir);
	my $max = $mm->max;
	return $max // 0;
}

sub origins_blob {
	my ($name, $index) = @_;
	return <<"EOM";
[publicinbox "$name"]
	address = $name\@integration.test
	description = Integration test list $index
	listid = $name.integration.test
EOM
}

# Run git with $input on stdin and return its stdout (dies on failure).
sub git_stdin {
	my ($git_dir, $input, @args) = @_;
	my ($rdr, $wtr);
	my $pid = open2($rdr, $wtr, 'git', "--git-dir=$git_dir", @args);
	print $wtr $input;
	close $wtr;
	local $/;
	my $out = <$rdr> // '';
	close $rdr;
	waitpid($pid, 0);
	die "git @args failed in $git_dir\n" if $? != 0;
	return $out;
}

# Write refs/meta/origins:i, the grokmirror/lore convention the controller
# reads inbox addresses from.  Done with plain git so it does not depend on
# the public-inbox version's Import API.
sub write_origins_ref {
	my ($dir, $name, $index) = @_;
	my $git_dir = File::Spec->catdir($dir, 'git', '0.git');
	my $blob = git_stdin($git_dir, origins_blob($name, $index),
		qw(hash-object -w --stdin));
	chomp $blob;
	my $tree = git_stdin($git_dir, "100644 blob $blob\ti\n", 'mktree');
	chomp $tree;
	my $commit = git_stdin($git_dir, "origins for $name\n",
		'commit-tree', $tree);
	chomp $commit;
	git_stdin($git_dir, '', 'update-ref', 'refs/meta/origins', $commit);
}

#: Create one v2 inbox with $messages original messages.
sub create_list {
	my (%arg) = @_;
	my $root = $arg{root};
	my $name = $arg{name};
	my $index = $arg{index};
	my $messages = $arg{messages} // 3;
	my $dir = File::Spec->catdir($root, $name);

	require PublicInbox::TestCommon;
	PublicInbox::TestCommon::create_inbox(
		$name,
		version => 2,
		indexlevel => 'basic',
		tmpdir => $dir,
		-no_gc => 1,
		sub {
			my ($writable) = @_;
			for my $n (0 .. $messages - 1) {
				$writable->add(make_eml(name => $name, index => $n))
					or die "add failed for $name message $n: $@\n";
			}
		},
	);

	write_origins_ref($dir, $name, $index);

	# public-inbox detects the v2 format by the presence of inbox.lock; the
	# importer does not create it, public-inbox-init does.
	open(my $lock, '>>', File::Spec->catfile($dir, 'inbox.lock'))
		or die "Cannot create inbox.lock for $name: $!\n";
	close($lock);

	# create_inbox() leaves its build markers and journals behind; they are
	# not part of a real mirror and would confuse later reruns.  inbox.lock
	# and open.lock are part of the v2 layout and must stay.
	for my $f (qw(creat.lock creat.stamp msgmap.sqlite3-journal)) {
		unlink File::Spec->catfile($dir, $f);
	}
	return $dir;
}

sub inbox_section {
	my ($name, $dir, $index) = @_;
	return <<"EOM";
[publicinbox "$name"]
	inboxdir = $dir
	url = /$name
	address = $name\@integration.test
	description = Integration test list $index
	listid = $name.integration.test
	indexlevel = basic

EOM
}

#: Write the upstream public-inbox config from scratch.
#:
#: grokmanifest=all makes /manifest.js.gz list every inbox; wwwListing=all
#: does the same for the HTML index.
sub write_upstream_config {
	my (%arg) = @_;
	my $path = $arg{path};
	my $root = $arg{root};
	my @names = @{$arg{names}};

	my $tmp = "$path.tmp";
	open(my $fh, '>', $tmp) or die "Cannot write $tmp: $!\n";
	print $fh <<'EOM';
; Generated by tests/integration/seed
[publicinbox]
	grokmanifest = all
	wwwListing = all

EOM
	for my $i (0 .. $#names) {
		my $name = $names[$i];
		print $fh inbox_section($name, File::Spec->catdir($root, $name), $i);
	}
	close($fh) or die "Cannot close $tmp: $!\n";
	rename($tmp, $path) or die "Cannot rename $tmp to $path: $!\n";
}

#: Append new inbox sections to an existing config (used by mutate.pl).
sub append_inbox_config {
	my (%arg) = @_;
	my $path = $arg{path};
	my @entries = @{$arg{entries}}; # [ [$name, $index], ... ]

	open(my $fh, '>>', $path) or die "Cannot append to $path: $!\n";
	for my $entry (@entries) {
		my ($name, $index) = @$entry;
		print $fh inbox_section($name, $arg{root} . "/$name", $index);
	}
	close($fh) or die "Cannot close $path: $!\n";
}

#: Run $code with the cwd inside a scratch dir under $root (create_inbox
#: writes its build tree relative to the cwd), then clean it up.
sub with_scratch {
	my ($root, $code) = @_;
	my $work = File::Spec->catdir($root, '.seed-work');
	remove_tree($work) if -d $work;
	make_path($work) or die "Cannot create $work: $!\n";

	my $previous = Cwd::getcwd();
	chdir $work or die "Cannot chdir $work: $!\n";
	my $ok = eval { $code->($work); 1 };
	my $error = $@;
	chdir $previous or die "Cannot chdir $previous: $!\n";
	remove_tree($work);
	die $error unless $ok;
}

#: PIDs whose argv names public-inbox-netd (the master and its workers).
sub netd_pids {
	my @pids;
	opendir(my $dh, '/proc') or return @pids;
	for my $entry (readdir $dh) {
		next unless $entry =~ /\A[0-9]+\z/;
		open(my $fh, '<', "/proc/$entry/cmdline") or next;
		my $raw = do { local $/; <$fh> } // '';
		close($fh);
		my @argv = grep { length } split /\0/, $raw;
		push @pids, $entry if grep { m!(?:^|/)public-inbox-netd\z! } @argv;
	}
	closedir $dh;
	return sort { $a <=> $b } @pids;
}

#: SIGHUP the upstream daemon so newly added inboxes are served.
sub reload_netd {
	my @pids = netd_pids();
	die "no public-inbox-netd process found to reload\n" unless @pids;
	kill 'HUP', @pids or die "cannot signal public-inbox-netd: $!\n";
	printf "reloaded public-inbox-netd (pids: %s)\n", join(', ', @pids);
}

1;
