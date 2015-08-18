# telepathy-glitter - an gitter connection manager for Telepathy
#
# Copyright (C) 2015 Diane Trout
#
# Based on telepathy-butterfly
#
# Copyright (C) 2006-2007 Ali Sabil <ali.sabil@gmail.com>
# Copyright (C) 2007 Johann Prieur <johann.prieur@gmail.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

import weakref
import logging

import dbus
import telepathy

from glitter.capabilities import GlitterCapabilities
#from glitter.handle import GlitterHandleFactory, network_to_extension
from glitter.contacts import GlitterContacts
from glitter.channel_manager import GlitterChannelManager
from glitter.rooms import GitterClient

__all__ = ['GlitterConnection']

logger = logging.getLogger('Glitter.Connection')


class GlitterConnection(
        telepathy.server.Connection,
        telepathy.server.ConnectionInterfaceRequests,
        GlitterCapabilities,
        GlitterContacts,
):
    def __init__(self, protocol, manager, parameters):
        protocol.check_parameters(parameters)

        try:
            account = parameters['account']

            self._manager = weakref.proxy(manager)
            self._account = {'account': parameters['account'],
                             'token': parameters['token']}
            self._channel_manager = GlitterChannelManager(self, protocol)

            # Call parent initializers
            telepathy.server.Connection.__init__(
                self, 'gitter', account, 'glitter', protocol)
            telepathy.server.ConnectionInterfaceRequests.__init__(self)
            GlitterCapabilities.__init__(self)
            GlitterContacts.__init__(self)

            self_handle = self.create_handle(
                telepathy.HANDLE_TYPE_CONTACT,
                self._account['account'])
            self.set_self_handle(self_handle)

            self.__disconnect_reason = telepathy.CONNECTION_STATUS_REASON_NONE_SPECIFIED
            self._initial_presence = None
            self._initial_personal_message = None

            logger.info("Connection to the account %s created" % account)

            self._implement_property_get(
                telepathy.CONNECTION,
                {'Interfaces': self.GetInterfaces,
                 'SelfHandle': lambda: int(self.GetSelfHandle()),
                 #'SelfID':,
                 'Status': self.GetStatus,
                 'HasImmortalHandles': lambda: True,
                 })

            self._implement_property_get(
                telepathy.CONNECTION_INTERFACE_REQUESTS,
                {'Channels': self.GetRequestChannels,
                 'RequestableChannelClasses': self._channel_manager.get_requestable_channel_classes,
                 })
        except Exception as e:
            logger.exception("Failed to create Connection: %s" % (str(e),))
            raise e

    @property
    def manager(self):
        return self._manager

    @property
    def gitter_client(self):
        return self._gitter_client

    def roomFromHandle(self, handle):
        """Retrieve a gitter client room given a telepathy handle
        """
        name = self._contact_handles.get(handle, None)
        if name:
            return self._gitter_client.rooms[name]

    @dbus.service.method(telepathy.CONN_INTERFACE,
                         in_signature='',
                         out_signature='',
                         sender_keyword='sender')
    def Connect(self, sender):
        if self._status == telepathy.CONNECTION_STATUS_DISCONNECTED:
            logger.info("Connecting")
            self.StatusChanged(telepathy.CONNECTION_STATUS_CONNECTING,
                               telepathy.CONNECTION_STATUS_REASON_NONE_SPECIFIED)
            self.__disconnect_reason = telepathy.CONNECTION_STATUS_REASON_NONE_SPECIFIED
            self._gitter_client = GitterClient(self, self._account['token'])
            print(self._gitter_client)
            self._gitter_client.connected.connect(
                lambda sender=sender: self.connected(sender))
            self._gitter_client.connect()

    def connected(self, sender):
        logger.info("Connected %s", sender)
        self.StatusChanged(telepathy.CONNECTION_STATUS_CONNECTED,
                           telepathy.CONNECTION_STATUS_REASON_NONE_SPECIFIED)
        self.update_handles(sender)
        self.check_connected()
        self._populate_capabilities()

    def Disconnect(self):
        logger.info("Disconnecting")
        self.__disconnect_reason = telepathy.CONNECTION_STATUS_REASON_REQUESTED
        self._gitter_client.disconnect()
        self._disconnected()

    def _disconnected(self):
        logger.info("Disconnected")
        self.StatusChanged(
            telepathy.CONNECTION_STATUS_DISCONNECTED,
            self.__disconnect_reason)
        self._channel_manager.close()
        self._manager.disconnected(self)

    def _generate_props(self, channel_type, handle, suppress_handler, initiator_handle=None):
        props = {
            telepathy.CHANNEL_INTERFACE + '.ChannelType': channel_type,
            telepathy.CHANNEL_INTERFACE + '.TargetHandle': handle.get_id(),
            telepathy.CHANNEL_INTERFACE + '.TargetHandleType': handle.get_type(),
            telepathy.CHANNEL_INTERFACE + '.Requested': suppress_handler
            }

        if initiator_handle is not None:
            if initiator_handle.get_type() is not telepathy.HANDLE_TYPE_NONE:
                props[telepathy.CHANNEL_INTERFACE + '.InitiatorHandle'] = \
                        initiator_handle.get_id()

        return props


    @dbus.service.method(telepathy.CONNECTION,
                         in_signature='suub',
                         out_signature='o',
                         async_callbacks=('_success', '_error'))
    def RequestChannel(self, type, handle_type, handle_id, suppress_handler,
            _success, _error):
        self.check_connected()
        channel_manager = self._channel_manager

        if handle_id == telepathy.HANDLE_TYPE_NONE:
            handle = telepathy.server.handle.NoneHandle()
        else:
            handle = self.handle(handle_type, handle_id)
        props = self._generate_props(channel_type, handle, suppress_handler)
        self._validate_handle(props)

        channel = channel_manager.channel_for_props(props, signal=False)

        _success(channel._object_path)
        self.signal_new_channels([channel])

    @dbus.service.method(dbus_interface=dbus.PROPERTIES_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface_name):
        logger.debug("GetAll: %s %d", interface_name, self._status)
        return super().GetAll(interface_name)


    ### Connection Interface Requests
    @dbus.service.method(
        dbus_interface=telepathy.CONNECTION_INTERFACE_REQUESTS,
        in_signature='a{sv}',
        out_signature='oa{sv}',
        async_callbacks=('_success', '_error'))
    def CreateChannel(self, request, _success, _error):
        self.check_connected()
        for k, v in request:
            print('Create', k, v)
        channel = request.get(telepathy.CHANNEL_INTERFACE + '.ChannelType')
        target_type = request.get(telepathy.CHANNEL_INTERFACE + '.TargetHandleTypee')
        handle = request.get(telepathy.CHANNEL_INTERFACE + '.TargetHandle')
        targetid = request.get(telepathy.CHANNEL_INTERFACE + '.TargetID')
        if handle is None and targetid is None:
            raise InvalidHandle()
        elif handle is None:
            pass
        elif targetid is None:
            handle = self.ensure_contact_handle(targetid)
        raise NotImplemented()

    @dbus.service.method(
        dbus_interface=telepathy.CONNECTION_INTERFACE_REQUESTS,
        in_signature='a{sv}',
        out_signature='boa{sv}',
        async_callbacks=('_success', '_error'))
    def EnsureChannel(self, request, _success, _error):
        logger.debug("EnsureChannel")
        self.check_connected()
        for key in request:
            print(key, request[key])
        channel_manager = self._channel_manager

        if handle_id == telepathy.HANDLE_TYPE_NONE:
            handle = telepathy.server.handle.NoneHandle()
        else:
            handle = self.handle(handle_type, handle_id)
        props = self._generate_props(type, handle, suppress_handler)
        self._validate_handle(props)

        channel = channel_manager.channel_for_props(request, signal=False)

        _success(True, channel._object_path, channel.get_immutable_properties())
        self.signal_new_channels([channel])

    def GetRequestChannels(self):
        ret = dbus.Dictionary({}, signature="oa{sv}")
        channels = self.ListChannels()
        for channel in channels:
            props = self._generate_props(channel._type, channel._handle, True)
            ret[channel._object_path] = props

        return ret
