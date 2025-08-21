//
// COPYRIGHT LICENSE: This information contains sample code provided in source code form. You may copy, 
// modify, and distribute these sample programs in any form without payment to IBM for the purposes of 
// developing, using, marketing or distributing application programs conforming to the application 
// programming interface for the operating platform for which the sample code is written. 
// Notwithstanding anything to the contrary, IBM PROVIDES THE SAMPLE SOURCE CODE ON AN "AS IS" BASIS 
// AND IBM DISCLAIMS ALL WARRANTIES, EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, ANY IMPLIED 
// WARRANTIES OR CONDITIONS OF MERCHANTABILITY, SATISFACTORY QUALITY, FITNESS FOR A PARTICULAR PURPOSE, 
// TITLE, AND ANY WARRANTY OR CONDITION OF NON-INFRINGEMENT. IBM SHALL NOT BE LIABLE FOR ANY DIRECT, 
// INDIRECT, INCIDENTAL, SPECIAL OR CONSEQUENTIAL DAMAGES ARISING OUT OF THE USE OR OPERATION OF THE 
// SAMPLE SOURCE CODE. IBM HAS NO OBLIGATION TO PROVIDE MAINTENANCE, SUPPORT, UPDATES, ENHANCEMENTS 
// OR MODIFICATIONS TO THE SAMPLE SOURCE CODE.  
//
// (C) COPYRIGHT International Business Machines Corp., 2001,2011
// All Rights Reserved * Licensed Materials - Property of IBM
//
package com.ibm.websphere.samples.pbw.bean;

import java.io.Serializable;
import java.util.Date;

import javax.annotation.Resource;
import javax.enterprise.context.Dependent;
import javax.inject.Named;
import javax.mail.Message;
import javax.mail.Multipart;
import javax.mail.Session;
import javax.mail.Transport;
import javax.mail.internet.InternetAddress;
import javax.mail.internet.MimeBodyPart;
import javax.mail.internet.MimeMessage;
import javax.mail.internet.MimeMultipart;
import javax.persistence.EntityManager;
import javax.persistence.PersistenceContext;

import com.ibm.websphere.samples.pbw.jpa.Customer;
import com.ibm.websphere.samples.pbw.jpa.Order;
import com.ibm.websphere.samples.pbw.utils.Util;

/**
 * MailerBean provides a transactional facade for access to Order information and notification of
 * the buyer of order state.
 * 
 */

@Named(value = "mailerbean")
@Dependent

public class MailerBean implements Serializable {
	private static final long serialVersionUID = 1L;
	// public static final String MAIL_SESSION = "java:comp/env/mail/PlantsByWebSphere";
	@Resource(name = "mail/PlantsByWebSphere")
	Session mailSession;

	@PersistenceContext(unitName = "PBW")

	EntityManager em;

	/**
	 * Create the email message.
	 *
	 * @param orderKey
	 *            The order number.
	 * @return The email message.
	 */
	private String createMessage(String orderKey) {
		Util.debug("creating email message for order:" + orderKey);
		StringBuffer msg = new StringBuffer();
		Order order = em.find(Order.class, orderKey);
		msg.append("Thank you for your order " + orderKey + ".\n");
		msg.append("Your Plants By WebSphere order will be shipped to:\n");
		msg.append("     " + order.getShipName() + "\n");
		msg.append("     " + order.getShipAddr1() + " " + order.getShipAddr2() + "\n");
		msg.append("     " + order.getShipCity() + ", " + order.getShipState() + " " + order.getShipZip() + "\n\n");
		msg.append("Please save it for your records.\n");
		return msg.toString();
	}

	/**
	 * Create the Subject line.
	 *
	 * @param orderKey
	 *            The order number.
	 * @return The Order number string.
	 */
	private String createSubjectLine(String orderKey) {
		StringBuffer msg = new StringBuffer();
		msg.append("Your order number " + orderKey);

		return msg.toString();
	}

	/**
	 * Create a mail message and send it.
	 *
	 * @param customerInfo
	 *            Customer information.
	 * @param orderKey
	 * @throws MailerAppException
	 */
	public void createAndSendMail(Customer customerInfo, String orderKey) throws MailerAppException {
		try {
			EMailMessage eMessage = new EMailMessage(createSubjectLine(orderKey), createMessage(orderKey),
					customerInfo.getCustomerID());

			Util.debug("Sending message" + "\nTo: " + eMessage.getEmailReceiver() + "\nSubject: "
					+ eMessage.getSubject() + "\nContents: " + eMessage.getHtmlContents());

			Util.debug("Sending message" + "\nTo: " + eMessage.getEmailReceiver() + "\nSubject: "
					+ eMessage.getSubject() + "\nContents: " + eMessage.getHtmlContents());

			MimeMessage msg = new MimeMessage(mailSession);
			msg.setFrom();

			msg.setRecipients(Message.RecipientType.TO, InternetAddress.parse(eMessage.getEmailReceiver(), false));

			msg.setSubject(eMessage.getSubject());
			MimeBodyPart mbp = new MimeBodyPart();
			mbp.setText(eMessage.getHtmlContents(), "us-ascii");
			msg.setHeader("X-Mailer", "JavaMailer");
			Multipart mp = new MimeMultipart();
			mp.addBodyPart(mbp);
			msg.setContent(mp);
			msg.setSentDate(new Date());

			Transport.send(msg);
			Util.debug("Mail sent successfully.");

		} catch (Exception e) {

			Util.debug("Error sending mail. Have mail resources been configured correctly?");
			Util.debug("createAndSendMail exception : " + e);
			e.printStackTrace();
			throw new MailerAppException("Failure while sending mail");
		}
	}
}
