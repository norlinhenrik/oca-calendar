# Copyright 2017 Laslabs Inc.
# Copyright 2018 Savoir-faire Linux
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl.html).

from datetime import datetime, time, timedelta
from dateutil.rrule import rrule, DAILY
from os import linesep
import pytz

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CalendarEvent(models.Model):

    _inherit = 'calendar.event'

    # https://github.com/odoo/odoo/blob/13.0/addons/calendar/models/calendar.py#L781
    # With empty timezone, 'allday' events have ambigous start/stop time, assuming UTC.
    event_tz = fields.Selection('_event_tz_get', string='Timezone', default=lambda self: self.env.context.get('tz') or self.user_id.tz)

    def _event_tz_get(self):
        # put POSIX 'Etc/*' entries at the end to avoid confusing users - see bug 1086728
        return [(tz, tz) for tz in sorted(pytz.all_timezones, key=lambda tz: tz if not tz.startswith('Etc/') else '_')]

    # timezone = pytz.timezone(self.event_tz) if self.event_tz else pytz.timezone(self._context.get('tz') or 'UTC')
    # timezone = self._context.get('tz') or self.env.user.partner_id.tz or 'UTC'
    # self_tz = self.with_context(tz=timezone)

    resource_ids = fields.Many2many(
        string='Resources',
        comodel_name='resource.resource',
    )

    #@api.multi
    def _event_in_past(self):
        self.ensure_one()
        stop_datetime = self.stop
        now_datetime = datetime.now()
        return stop_datetime < now_datetime

    #@api.multi
    #@api.constrains('resource_ids', 'start', 'stop') # recurring changes
    def _check_resource_ids_double_book(self):
        for record in self:
            resources = record.resource_ids.filtered(
                lambda s: s.allow_double_book is False
            )
            if record._event_in_past() or not resources:
                continue
            overlaps = self.env['calendar.event'].search([
                ('id', '!=', record.id),
                ('resource_ids', '!=', False),
                ('start', '<', record.stop),
                ('stop', '>', record.start),
            ], limit=1)
            for resource in overlaps.mapped(lambda s: s.resource_ids):
                if resource in resources:
                    raise ValidationError(
                        _(
                            'The resource, %s, cannot be double-booked '
                            'with any overlapping meetings or events.',
                        )
                        % resource.name,
                    )

    #@api.multi
    #@api.constrains('resource_ids', 'categ_ids')
    def _check_resource_ids_categ_ids(self):

        for record in self.filtered(lambda x: not x._event_in_past()):

            if not record.categ_ids:
                continue

            for resource in record.resource_ids:
                categs = record.categ_ids.filtered(
                    lambda s: s not in resource.allowed_event_types
                )
                if categs:
                    raise ValidationError(
                        _(
                            "The resource, '%s', is not allowed in the "
                            "following event types: \n%s",
                        )
                        % (
                            resource.name,
                            ', '.join([
                                categ.name for categ in categs
                            ]),
                        )
                    )

    #@api.multi
    def _get_event_date_list(self):
        """ Builds a list of datetimes of the days of the event

        Each datetime in the list is the beginning of a
        separate day.

        """
        self.ensure_one()
        start_date = self.start.date()
        stop_datetime = self.stop

        if stop_datetime.time() == time(0, 0):
            stop_datetime -= timedelta(days=1)

        return list(
            rrule(DAILY, dtstart=start_date, until=stop_datetime.date())
        )

    def _get_event_datetime_list(self):
        self.ensure_one()
        event_tz = pytz.timezone(self.event_tz) if self.event_tz else pytz.timezone(self._context.get('tz') or 'UTC')
        #timezone('US/Eastern').localize(datetime)
        #.replace(tzinfo=None) # shortcut
        #.astimezone(tz)
        # (1) local tz (2) remove time (3) as UTC
        if self.recurrency:
            dt_list = self._get_recurrent_dates_by_event()
            if self.allday:
                dt_list2 = [(
                    start.date(),
                    stop.date()
                ) for start, stop in dt_list]
        
        # allday?

    #@api.multi
    #@api.constrains('resource_ids', 'start', 'stop') # recurring changes
    def _check__a_resource_ids_working_times(self):
        my_interval = self.get_interval('day', tz=None)
        my_recurrent_ids = self.get_recurrent_ids([], order=None)
        
        # works only if recurrent == True
        if self.recurrency == True:
            my_recurrent_dates = self._get_recurrent_dates_by_event() # list of tuples [(start,stop)]



        ResourceCalendar = self.env['resource.calendar']
        for record in self.filtered(lambda x: not x._event_in_past()):

            event_start = record.start.replace(tzinfo=pytz.utc)
            event_stop = record.stop.replace(tzinfo=pytz.utc)
            event_days = record._get_event_date_list()
            event_tz = pytz.timezone(record.event_tz) if record.event_tz else pytz.timezone(record._context.get('tz') or 'UTC')

            for resource in record.resource_ids.filtered(
                    'calendar_id'):

                available_intervals = []
                conflict_intervals = []

                for day in event_days:
                    
                    datetime_start = event_tz.localize(datetime.combine(day, time(00, 00, 00)))
                    datetime_end = event_tz.localize(datetime.combine(day, time(23, 59, 59)))

                    intervals = \
                        resource.calendar_id._work_intervals(
                            datetime_start,
                            datetime_end,
                        )

                    if not intervals:
                        conflict_intervals.append(
                            (datetime_start, datetime_end),
                        )
                    else:
                        available_intervals += intervals

                if available_intervals and not record.allday:
                    conflict_intervals = ResourceCalendar.\
                        _get_conflicting_unavailable_intervals(
                            available_intervals, event_start, event_stop,
                        )

                if not conflict_intervals:
                    continue

                conflict_intervals = ResourceCalendar.\
                    _clean_datetime_intervals(
                        conflict_intervals,
                    )

                raise ValidationError(
                    _(
                        'The resource, %s, is not available during '
                        'the following dates and times which are '
                        'conflicting with the event:%s%s',
                    )
                    % (
                        resource.name,
                        2 * linesep,
                        #self._format_datetime_intervals_to_str(
                        #    conflict_intervals,
                        #),
                        conflict_intervals,
                    )
                )
